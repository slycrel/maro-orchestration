import io, json, sys, types

SRC = sys.argv[1]
sys.path.insert(0, SRC)

import loop_finalize as lf


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


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FalsyAdapter:
    def __bool__(self):
        return False


class DeadModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        raise ImportError("no module")


class PartialModule(DeadModule):
    pass


def tag(adapter):
    if adapter is None:
        return "none"
    return "truthy" if adapter else "falsy"


def raiser(spec):
    """spec is [class, message] or None."""
    if not spec:
        return
    cls = {"Boom": Boom, "ImportError": ImportError,
           "ValueError": ValueError}[spec[0]]
    raise cls(spec[1])


def run_one(sc):
    calls, logs = [], []

    def rec(_call, /, **kw):
        calls.append(dict(kw, call=_call))

    drop = set(sc["drop_names"])

    def mod(name, missing, **fns):
        if missing:
            return DeadModule(name)
        m = PartialModule(name)
        for k, v in fns.items():
            if k not in drop:
                setattr(m, k, v)
        return m

    def run_skill_maintenance(adapter=None):
        rec("run_skill_maintenance", adapter=tag(adapter))
        raiser(sc["skill_raise"])

    def run_health_probes():
        rec("run_health_probes")
        raiser(sc["health_raise"])

    def run_statistical_scans(verbose=False):
        rec("run_statistical_scans", verbose=verbose)
        raiser(sc["scans_raise"])
        return list(sc["scans"])

    def _save_suggestions(suggestions):
        rec("_save_suggestions", n=len(suggestions))
        raiser(sc["save_raise"])

    def cfg_get(key, default=None):
        rec("config_get", key=key, default=default)
        raiser(sc["cfg_raise"])
        cfg = sc["cfg"]
        return cfg[key] if key in cfg else default

    def evolver_cadence_tick(cadence):
        rec("evolver_cadence_tick", cadence=cadence)
        raiser(sc["evolver_tick_raise"])
        return sc["evolver_tick"]

    def run_evolver(adapter=None, verbose=False):
        rec("run_evolver", adapter=tag(adapter), verbose=verbose)
        raiser(sc["evolver_raise"])
        if sc["evo_no_attrs"]:
            return Obj()
        return Obj(outcomes_reviewed=sc["evo_reviewed"],
                   suggestions=sc["evo_suggestions"])

    def inspector_cadence_tick(cadence, deep_every):
        rec("inspector_cadence_tick", cadence=cadence, deep_every=deep_every)
        raiser(sc["insp_tick_raise"])
        return sc["insp_mode"]

    def run_inspector(limit=0, adapter=None, verbose=False):
        rec("run_inspector", limit=limit, adapter=tag(adapter),
            verbose=verbose)
        raiser(sc["insp_raise"])
        if sc["insp_no_attrs"]:
            return Obj()
        return Obj(inspected_sessions=sc["insp_sessions"])

    sys.modules["evolver"] = mod(
        "evolver", sc["missing_evolver"],
        run_skill_maintenance=run_skill_maintenance,
        run_statistical_scans=run_statistical_scans,
        run_evolver=run_evolver)
    sys.modules["system_health"] = mod(
        "system_health", sc["missing_system_health"],
        run_health_probes=run_health_probes)
    sys.modules["evolver_store"] = mod(
        "evolver_store", sc["missing_evolver_store"],
        _save_suggestions=_save_suggestions,
        evolver_cadence_tick=evolver_cadence_tick)
    sys.modules["config"] = mod("config", sc["missing_config"], get=cfg_get)
    sys.modules["inspector"] = mod(
        "inspector", sc["missing_inspector"],
        DEEP_PASS_LIMIT=sc["deep_pass_limit"],
        inspector_cadence_tick=inspector_cadence_tick,
        run_inspector=run_inspector)

    lf.log = Log(logs)

    adapter = {"none": None, "truthy": Obj(),
               "falsy": FalsyAdapter()}[sc["adapter"]]
    lf.run_post_run_maintenance(adapter=adapter, verbose=sc["verbose"])
    return {"name": sc["name"], "calls": calls, "logs": logs}


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
print(json.dumps([run_one(sc) for sc in spec]))
