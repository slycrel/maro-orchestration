import contextlib, io, json, sys, types

SRC = sys.argv[1]
sys.path.insert(0, SRC)

import loop_finalize as lf
from loop_types import StepOutcome

# Captured BEFORE any scenario patches the module. Reading these off `lf`
# inside run_one would chain each scenario's wrapper onto the previous
# one's, and the previous one's recorder writes into a call list this
# process has not printed yet — scenario 1's record would grow rows from
# scenario 40.
ORIG_DEFER = lf.defer_maintenance_post_notify


class Boom(RuntimeError):
    pass


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


class Diag:
    def __init__(self, d):
        self.failure_class = d["failure_class"]
        self.recommendation = d["recommendation"]
        self._summary = d["summary"]

    def summary(self):
        return self._summary


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FalsyAdapter:
    def __bool__(self):
        return False


class DeadModule(types.ModuleType):
    def __getattr__(self, name):
        raise ImportError("no module")


class PartialModule(types.ModuleType):
    """A module missing SOME of its names.

    `from memory import reflect_and_record, record_step_trace` is one
    statement: dropping either name fails the whole import, and the
    handler that catches it is the one at the top of the try — not the
    one wrapping the call site that would have used it. A test that can
    only delete a whole module cannot tell those two apart, so the guard
    composition in the port would be unfalsifiable.

    The text is ImportError("no module") rather than CPython's own
    "cannot import name ..." because the port's errImport is a single
    sentinel; the SHAPE under test is which handler catches it and when,
    not the wording the interpreter would have used.
    """

    def __getattr__(self, name):
        # `from X import y` runs _handle_fromlist first, which probes
        # __path__ through hasattr — and hasattr only swallows
        # AttributeError. Raising ImportError for a dunder would fail
        # EVERY import from this module, not the dropped names.
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        raise ImportError("no module")


def tag(adapter):
    if adapter is None:
        return "none"
    return "truthy" if adapter else "falsy"


def raiser(msg):
    if msg:
        raise Boom(msg)


def run_one(sc):
    calls, logs = [], []
    # The registry is a module global here and a fresh value on the Go
    # side, so it is emptied per scenario to make the two comparable.
    lf._POST_NOTIFY_MAINTENANCE.clear()

    # Positional-only: save_skill records a kwarg literally called `name`,
    # which a plain first parameter would collide with — and the collision
    # surfaces as a TypeError swallowed by the crystallize handler, i.e. a
    # probe bug wearing the costume of a port divergence.
    def rec(_call, /, **kw):
        calls.append(dict(kw, call=_call))

    # ---- introspect ------------------------------------------------
    def diagnose_loop(loop_id, project=""):
        rec("diagnose_loop", loop_id=loop_id, project=project)
        raiser(sc["diag_raise"])
        return Diag(sc["diag"])

    def save_diagnosis(d):
        rec("save_diagnosis", failure_class=d.failure_class)
        raiser(sc["save_diag_raise"])

    def load_loop_events(loop_id):
        rec("_load_loop_events", loop_id=loop_id)
        raiser(sc["events_raise"])
        return ["ev"]

    def build_step_profiles(events):
        rec("_build_step_profiles", events=list(events))
        raiser(sc["profiles_raise"])
        return ["prof"]

    def run_lenses(d, profiles):
        rec("run_lenses", failure_class=d.failure_class,
            profiles=list(profiles))
        raiser(sc["lenses_raise"])
        return [Obj(lens_name=l["lens_name"], action=l["action"])
                for l in sc["lenses"]]

    def aggregate_lenses(d, results):
        rec("aggregate_lenses", n=len(results))
        raiser(sc["agg_raise"])
        a = sc["agg"]
        return Obj(confidence=a["confidence"],
                   lens_agreement=a["lens_agreement"],
                   primary_action=a["primary_action"])

    def plan_recovery(d, use_advisor=False):
        rec("plan_recovery", failure_class=d.failure_class,
            use_advisor=use_advisor)
        raiser(sc["recovery_raise"])
        r = sc["recovery"]
        if r is None:
            return None
        return Obj(auto_apply=r["auto_apply"], risk=r["risk"],
                   action=r["action"])

    # ---- memory ----------------------------------------------------
    def record_tiered_lesson(**kw):
        rec("record_tiered_lesson", **kw)
        raiser(sc["tiered_raise"])

    def _store_lesson(**kw):
        rec("_store_lesson", **kw)
        raiser(sc["store_lesson_raise"])

    def reflect_and_record(**kw):
        rec("reflect_and_record", **dict(kw, adapter=tag(kw["adapter"])))
        raiser(sc["reflect_raise"])
        if sc["reflect_none"]:
            return None
        return Obj(outcome_id=sc["outcome_id"])

    def record_step_trace(outcome_id, goal, steps, task_type=""):
        rec("record_step_trace", outcome_id=outcome_id, goal=goal,
            nsteps=len(steps), task_type=task_type)
        raiser(sc["trace_raise"])

    def extract_step_lessons(goal, steps, task_type="", adapter=None,
                             loop_id=""):
        rec("extract_step_lessons", goal=goal, nsteps=len(steps),
            task_type=task_type, adapter=tag(adapter), loop_id=loop_id)
        raiser(sc["steplessons_raise"])

    # ---- mint_grounding --------------------------------------------
    def ground_lessons_for_run(texts, loop_id):
        rec("ground_lessons_for_run", texts=list(texts), loop_id=loop_id)
        raiser(sc["grounding_raise"])
        return sc["grounding"]

    # ---- portability -----------------------------------------------
    def weighting_enabled():
        rec("weighting_enabled")
        raiser(sc["weighting_raise"])
        return sc["weighting"]

    def refresh_cache():
        rec("refresh_cache")
        raiser(sc["refresh_raise"])

    # ---- tail_jobs / maintenance -----------------------------------
    def record_maintenance(handle_id, loop_id="", adapter=None, verbose=False):
        rec("record_maintenance", handle_id=handle_id, loop_id=loop_id,
            adapter=tag(adapter), verbose=verbose)
        raiser(sc["record_maint_raise"])
        return sc["record_maint"]

    def run_post_run_maintenance(*, adapter=None, verbose=False):
        rec("run_post_run_maintenance", adapter=tag(adapter), verbose=verbose)

    def defer_maintenance_post_notify(handle_id, fn):
        rec("defer_maintenance_post_notify", handle_id=handle_id)
        raiser(sc["defer_raise"])
        ORIG_DEFER(handle_id, fn)

    @contextlib.contextmanager
    def loop_id_scope(loop_id):
        rec("loop_id_scope", loop_id=loop_id)
        raiser(sc["scope_raise"])
        yield

    # ---- telegram ---------------------------------------------------
    def telegram_notify(msg):
        rec("telegram_notify", msg=msg)
        raiser(sc["telegram_raise"])

    # ---- skills / evolver (the crystallize seam) --------------------
    class Skill:
        def __init__(self, name):
            self.name = name

    def load_skills():
        rec("load_skills")
        return [Skill(n) for n in sc["existing"]]

    def extract_skills(outcomes, adapter):
        o = outcomes[0]
        rec("extract_skills", n=len(outcomes), adapter=tag(adapter),
            goal=o["goal"], status=o["status"], task_type=o["task_type"],
            summary=o["summary"], nsteps=len(o["steps"]),
            project=o["project"])
        return [Skill(n) for n in sc["extracted"]]

    def save_skill(skill):
        rec("save_skill", name=skill.name)

    def synthesize_skill(**kw):
        rec("synthesize_skill", **dict(kw, adapter=tag(kw["adapter"])))

    drop = set(sc["drop_names"])

    def mod(name, missing, **fns):
        if missing:
            return DeadModule(name)
        m = PartialModule(name)
        for k, v in fns.items():
            if k not in drop:
                setattr(m, k, v)
        return m

    sys.modules["introspect"] = mod(
        "introspect", sc["missing_introspect"],
        diagnose_loop=diagnose_loop, save_diagnosis=save_diagnosis,
        run_lenses=run_lenses, aggregate_lenses=aggregate_lenses,
        plan_recovery=plan_recovery, _build_step_profiles=build_step_profiles,
        _load_loop_events=load_loop_events)
    sys.modules["memory"] = mod(
        "memory", sc["missing_memory"],
        record_tiered_lesson=record_tiered_lesson, _store_lesson=_store_lesson,
        reflect_and_record=reflect_and_record,
        record_step_trace=record_step_trace,
        extract_step_lessons=extract_step_lessons)
    sys.modules["mint_grounding"] = mod(
        "mint_grounding", sc["missing_mint_grounding"],
        ground_lessons_for_run=ground_lessons_for_run)
    sys.modules["portability"] = mod(
        "portability", sc["missing_portability"],
        weighting_enabled=weighting_enabled, refresh_cache=refresh_cache)
    sys.modules["tail_jobs"] = mod(
        "tail_jobs", sc["missing_tailjobs"],
        record_maintenance=record_maintenance)
    sys.modules["captains_log"] = mod(
        "captains_log", False, loop_id_scope=loop_id_scope)
    sys.modules["telegram_listener"] = mod(
        "telegram_listener", sc["missing_telegram"],
        telegram_notify=telegram_notify)
    sys.modules["skills"] = mod("skills", False, load_skills=load_skills,
                                extract_skills=extract_skills,
                                save_skill=save_skill)
    sys.modules["evolver"] = mod("evolver", False,
                                 synthesize_skill=synthesize_skill)

    lf.log = Log(logs)
    lf.run_post_run_maintenance = run_post_run_maintenance
    lf.defer_maintenance_post_notify = defer_maintenance_post_notify

    if sc["model_key"]:
        adapter_obj = Obj(model_key=sc["model_key"])
    else:
        adapter_obj = Obj()
    adapter = {"none": None, "truthy": adapter_obj,
               "falsy": FalsyAdapter()}[sc["adapter"]]

    outcomes = [StepOutcome(index=i + 1, text=s["text"], status=s["status"],
                            result=s["result"], iteration=0)
                for i, s in enumerate(sc["steps"])]

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        lf._finalize_loop(
            sc["loop_id"], sc["goal"], sc["project"], sc["loop_status"],
            outcomes, adapter,
            dry_run=sc["dry_run"], verbose=sc["verbose"],
            total_tokens_in=sc["tokens_in"], total_tokens_out=sc["tokens_out"],
            elapsed_ms=sc["elapsed_ms"],
            had_no_matching_skill=sc["had_no_matching"],
            failure_chain=sc["failure_chain"],
            recovery_steps=sc["recovery_steps"],
            defer_learning=sc["defer_learning"],
            defer_maintenance=sc["defer_maintenance"],
            measurement_class=sc["measurement_class"],
            handle_id=sc["handle_id"], stop_verdict=sc["stop_verdict"],
            stop_evidence=sc["stop_evidence"], pause_reason=sc["pause_reason"])
        drained = -1
        if sc["drain"]:
            drained = lf.drain_deferred_maintenance(sc["handle_id"])
    return {"name": sc["name"], "calls": calls, "logs": logs,
            "stderr": err.getvalue(), "drained": drained}


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
print(json.dumps([run_one(sc) for sc in spec]))
