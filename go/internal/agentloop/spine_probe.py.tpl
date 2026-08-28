import builtins, contextlib, io, json, sys, types

SRC = sys.argv[1]
sys.path.insert(0, SRC)

import agent_loop as al
import loop_types as lt

# Captured ONCE. run_one rebinds al.run_agent_loop to intercept the
# auto-recovery recursion, so a per-scenario capture would hand the second
# scenario the previous scenario's fake.
REAL_RUN = al.run_agent_loop


class PartialModule(types.ModuleType):
    """A module missing SOME of its names — for `from X import y`.

    The message is the port's `errImportFence`: CPython's real one
    interpolates the module's FILE path, which no differential can
    reproduce, so both sides agree on a controlled string instead.
    """

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        raise ImportError("no module")


class Log:
    def __init__(self, sink):
        self.sink = sink

    def _at(self, level, fmt, a):
        self.sink.append({"level": level, "msg": fmt % a})

    def info(self, fmt, *a):
        self._at("info", fmt, a)

    def warning(self, fmt, *a):
        self._at("warning", fmt, a)

    def debug(self, fmt, *a):
        self._at("debug", fmt, a)

    def error(self, fmt, *a):
        self._at("error", fmt, a)


class Marker:
    """Anything the spine only PASSES ON: it must arrive unchanged."""

    def __init__(self, tag):
        self.tag = tag


def pv(v):
    """One rendering, used by both engines.

    Dicts render as ORDERED pairs rather than as objects, because the
    order a spine builds keyword arguments in is part of what is being
    compared and a JSON object does not preserve it.
    """
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Marker):
        return "<%s>" % v.tag
    if isinstance(v, lt.LoopStateMachine):
        return "<ctx>"
    if isinstance(v, lt.LoopResult):
        return {"__result__": [v.loop_id, v.status, v.elapsed_ms,
                               v.stuck_reason]}
    if isinstance(v, (list, tuple)):
        return [pv(x) for x in v]
    if isinstance(v, dict):
        return [[k, pv(x)] for k, x in v.items()]
    if callable(v):
        return "<fn>"
    return "<%s>" % type(v).__name__


def run_one(sc):
    calls, logs = [], []
    counts = {}

    def rec(name, kw):
        calls.append({"call": name,
                      "kw": [[k, pv(x)] for k, x in kw.items()]})

    def raiser(key):
        spec = sc["raises"].get(key)
        if not spec:
            return
        counts[key] = counts.get(key, 0) + 1
        if len(spec) > 2 and spec[2] != str(counts[key]):
            return
        cls = getattr(builtins, spec[0])
        raise cls(spec[1])

    drop = set(sc["drop_names"])
    dead = set(sc["dead_modules"])

    def mod(name, partial=True):
        return PartialModule(name) if partial else types.ModuleType(name)

    def install(name, m):
        if name in dead:
            sys.modules.pop(name, None)
            return
        sys.modules[name] = m

    def mkresult(d):
        if d is None:
            return None
        return lt.LoopResult(
            loop_id=d.get("loop_id", "L1"), goal=sc["goal"],
            project=sc["ctx_project"],
            steps=[lt.StepOutcome(index=i, text="", result="", iteration=0,
                                  status=s.get("status", ""))
                   for i, s in enumerate(d.get("steps") or [])],
            status=d.get("status", "done"),
            stuck_reason=d.get("stuck_reason"),
            elapsed_ms=d.get("elapsed_ms", 0))

    # --- the fence, which must come up quietly --------------------------
    llm = mod("llm")
    llm.MODEL_CHEAP, llm.MODEL_MID, llm.MODEL_POWER = "cheap", "mid", "power"
    llm.set_default_subprocess_cwd = lambda d: None
    llm.set_default_container_rw_roots = lambda r: None
    if "arm_cost_meter" not in drop:
        def arm_cost_meter(ceiling):
            rec("arm_cost_meter", {"ceiling": ceiling})
            raiser("arm_cost_meter")

            def disarm():
                rec("disarm_cost_meter", {})
                raiser("disarm_cost_meter")
            return disarm
        llm.arm_cost_meter = arm_cost_meter
    install("llm", llm)

    ce = mod("container_exec", partial=False)
    ce.set_container_suppressed = lambda v: None
    ce.set_introspection_run = lambda v: None
    ce.container_configured = lambda: False
    install("container_exec", ce)

    cfg = mod("config")
    if "config_get" not in drop:
        def cfg_get(key, default=None):
            # The FENCE reads this module too, twenty lines earlier and
            # outside this unit. Only the spine's own key is recorded, so a
            # fixture cannot accidentally compare the fence's read.
            if key != "budget.runaway_multiplier":
                return default
            rec("config_get", {"key": key, "default": default})
            raiser("config_get")
            return sc["runaway_multiplier"]
        cfg.get = cfg_get
    cfg.workspace_root = lambda: _WS
    install("config", cfg)

    ac = mod("artifact_check")
    ac.goal_declared_roots = lambda g: []
    install("artifact_check", ac)

    # --- the spine's own module boundaries ------------------------------
    rt = mod("run_trace")
    if "record_edge" not in drop:
        def record_edge(frm, to, **kw):
            # The real record_edge normalises `loop_id or ""`, which is why
            # set_phase's `... or None` and the spine's raw ctx.loop_id end
            # at the same value.
            kw["loop_id"] = kw.get("loop_id") or ""
            rec("record_edge", dict({"from": frm, "to": to}, **kw))
            raiser("record_edge")
        rt.record_edge = record_edge
    install("run_trace", rt)

    lr = mod("loop_report")
    if "write_run_report" not in drop:
        def write_run_report(**kw):
            rec("write_run_report", kw)
            raiser("write_run_report")
        lr.write_run_report = write_run_report
    if "write_runs_index" not in drop:
        def write_runs_index(force=False):
            rec("write_runs_index", {"force": force})
            raiser("write_runs_index")
        lr.write_runs_index = write_runs_index
    install("loop_report", lr)

    intro = mod("introspect")
    if "diagnose_loop" not in drop:
        def diagnose_loop(loop_id):
            rec("diagnose_loop", {"loop_id": loop_id})
            raiser("diagnose_loop")
            return _DIAG
        intro.diagnose_loop = diagnose_loop
    if "plan_recovery" not in drop:
        def plan_recovery(diag):
            rec("plan_recovery", {"diag": pv(diag)})
            raiser("plan_recovery")
            return _PLAN
        intro.plan_recovery = plan_recovery
    install("introspect", intro)

    clog = mod("captains_log")
    clog.AUTO_RECOVERY = "auto_recovery"
    # run_agent_loop imports loop_id_scope from this module at its TOP,
    # outside every handler — so captains_log can never be the missing
    # module here, only a missing name. Same shape as the fence's dead-llm
    # fixture (L59, amendment 3).
    clog.loop_id_scope = lambda lid: contextlib.nullcontext()
    if "log_event" not in drop:
        def log_event(**kw):
            rec("log_event", kw)
            raiser("log_event")
        clog.log_event = log_event
    install("captains_log", clog)

    # --- the diagnosis and the plan, whose ATTRIBUTES can raise ---------
    class Attrs:
        def __init__(self, d, prefix):
            self._d = d
            self._p = prefix

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            raiser(self._p + "." + name)
            if name not in self._d:
                raise AttributeError(name)
            return self._d[name]

        # NO __bool__. plan_recovery returns a RecoveryPlan dataclass or
        # None, and a dataclass without __bool__ is always truthy — so
        # `if _recovery` is a None check and nothing else. A probe object
        # that could be falsy would be testing a state production has not
        # got.

    _DIAG = Attrs(sc["diagnosis"], "diag") if sc["diagnosis"] is not None \
        else None
    _PLAN = Attrs(sc["plan"], "plan") if sc["plan"] is not None else None

    class WS:
        def __truediv__(self, other):
            return self

        def mkdir(self, parents=False, exist_ok=False):
            pass
    _WS = WS()

    # --- the six phase functions ----------------------------------------
    def decompose_goal(ctx, *, preset_steps, max_steps, knowledge_sub_goals,
                       permission_context):
        rec("_decompose_goal", {
            "ctx": ctx, "preset_steps": preset_steps, "max_steps": max_steps,
            "knowledge_sub_goals": knowledge_sub_goals,
            "permission_context": permission_context})
        raiser("_decompose_goal")
        return tuple(sc["decompose_return"])
    al._decompose_goal = decompose_goal

    def preflight_checks(ctx, steps, *, resume_from_loop_id,
                         parallel_fan_out):
        rec("_preflight_checks", {
            "ctx": ctx, "steps": steps,
            "resume_from_loop_id": resume_from_loop_id,
            "parallel_fan_out": parallel_fan_out})
        raiser("_preflight_checks")
        return (sc["preflight_steps"], dict(sc["pf"]),
                mkresult(sc["preflight_early"]))
    al._preflight_checks = preflight_checks

    def run_parallel_path(ctx, steps, *, clean_steps, deps, levels,
                          parallel_levels, parallel_fan_out, proj_fanout_dir,
                          loop_shared_ctx, use_dag, resolve_tools_fn):
        rec("_run_parallel_path", {
            "ctx": ctx, "steps": steps, "clean_steps": clean_steps,
            "deps": deps, "levels": levels,
            "parallel_levels": parallel_levels,
            "parallel_fan_out": parallel_fan_out,
            "proj_fanout_dir": proj_fanout_dir,
            "loop_shared_ctx": loop_shared_ctx, "use_dag": use_dag,
            "resolve_tools_fn": resolve_tools_fn})
        raiser("_run_parallel_path")
        for _ in range(sc["resolve_tools_calls"]):
            rec("resolve_tools_result", {"tools": resolve_tools_fn()})
        return mkresult(sc["parallel_result"])
    al._run_parallel_path = run_parallel_path

    def prepare_execution(ctx, steps, manifest_steps):
        rec("_prepare_execution", {"ctx": ctx, "steps": steps,
                                   "manifest_steps": manifest_steps})
        raiser("_prepare_execution")
        return tuple(sc["prepare_return"])
    al._prepare_execution = prepare_execution

    def execute_main_loop(ctx, steps, step_indices, *, resume_completed,
                          resume_executor_session, prereq_context, pf_review,
                          levels, manifest_steps, replan_count,
                          loop_shared_ctx, resolve_tools_fn, tier_order,
                          parallel_fan_out):
        rec("_execute_main_loop", {
            "ctx": ctx, "steps": steps, "step_indices": step_indices,
            "resume_completed": resume_completed,
            "resume_executor_session": resume_executor_session,
            "prereq_context": prereq_context, "pf_review": pf_review,
            "levels": levels, "manifest_steps": manifest_steps,
            "replan_count": replan_count, "loop_shared_ctx": loop_shared_ctx,
            "resolve_tools_fn": resolve_tools_fn, "tier_order": tier_order,
            "parallel_fan_out": parallel_fan_out})
        raiser("_execute_main_loop")
        return dict(sc["ex"])
    al._execute_main_loop = execute_main_loop

    def build_result_and_finalize(ctx, **kw):
        rec("_build_result_and_finalize", dict({"ctx": ctx}, **kw))
        raiser("_build_result_and_finalize")
        return mkresult(sc["finalize_result"])
    al._build_result_and_finalize = build_result_and_finalize

    def write_loop_log(**kw):
        rec("_write_loop_log", kw)
        raiser("_write_loop_log")
    al._write_loop_log = write_loop_log

    tool_calls = [0]

    def get_tools_for_role(role, deny):
        tool_calls[0] += 1
        rec("_get_tools_for_role", {"role": role, "deny": deny})
        raiser("_get_tools_for_role")
        # A different answer per call: a registry that gained a tool
        # between two steps is the whole reason the closure re-queries.
        return ["role-tool-%d" % tool_calls[0]]
    al._get_tools_for_role = get_tools_for_role
    al._EXECUTE_TOOLS = ["default-tool"]

    # --- the context ----------------------------------------------------
    ctx = lt.LoopStateMachine(
        loop_id=sc["loop_id"], goal=sc["goal"], project=sc["ctx_project"])
    if sc["start_phase"]:
        ctx.phase = sc["start_phase"]
    ctx.start_ts = "2026-01-01T00:00:00Z"
    ctx.started_at = 100.0
    ctx.cost_budget = sc["cost_budget"]
    ctx.dry_run = sc["ctx_dry_run"]
    ctx.defer_learning = sc["defer_learning"]
    ctx.defer_maintenance = sc["defer_maintenance"]
    ctx.measurement_class = sc["measurement_class"]
    ctx.handle_id = sc["handle_id"]
    ctx.adapter = Marker("adapter")
    ctx.interrupt_queue = Marker("interrupt_queue")
    ctx.injections = [{"kind": "note"}] if sc["injections"] else []
    if sc["perm_ctx"]:
        class PC:
            role = sc["perm_ctx"]["role"]
            deny_patterns = sc["perm_ctx"]["deny_patterns"]
        ctx.perm_ctx = PC()

    # The FENCE runs before the spine and is another unit's differential.
    # These two keep it off the real filesystem: _project_dir_root reaches
    # config.projects_dir through orch_items, and the fence MKDIRS what it
    # returns — a probe that let that through would create directories
    # under the live workspace.
    al._project_dir_root = lambda: _WS
    al._goal_to_slug = lambda g: "slug"

    al.log = Log(logs)
    al._initialize_loop = lambda *a, **k: (ctx, None)

    # The recursion is a MODULE-GLOBAL lookup, so rebinding the name here
    # intercepts the auto-recovery re-run without touching the call we make
    # ourselves.
    def fake_run(**kw):
        rec("run_agent_loop", kw)
        raiser("run_agent_loop")
        return mkresult(sc["recursion_result"])
    al.run_agent_loop = fake_run

    blocker = _pyprobe_block(dead)
    result, exc = None, None
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            r = REAL_RUN(sc["goal"], project=sc["project"],
                         max_steps=sc["max_steps"],
                         dry_run=sc["dry_run"],
                         _recovery_in_progress=sc["recovery_in_progress"])
        result = pv(r)
    except BaseException as e:
        exc = [type(e).__name__, str(e)]
    finally:
        _pyprobe_unblock(blocker)

    return {"name": sc["name"], "calls": calls, "logs": logs,
            "result": result, "exc": exc, "phase": ctx.phase}


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
print(json.dumps([run_one(sc) for sc in spec]))
