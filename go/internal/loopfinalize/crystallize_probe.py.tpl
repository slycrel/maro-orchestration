import contextlib, io, json, sys, types

SRC = sys.argv[1]
sys.path.insert(0, SRC)

import loop_finalize as lf
from loop_types import StepOutcome


class Boom(RuntimeError):
    pass


class FalsyAdapter:
    """An adapter object that is not None and is not truthy.

    It exists to separate `adapter if adapter else None` (the extraction
    call) from `adapter=adapter` (the synthesis call). With a plain object
    both spellings agree and the asymmetry is invisible.
    """

    def __bool__(self):
        return False


class Skill:
    def __init__(self, name):
        self.name = name


class Log:
    def __init__(self, sink):
        self.sink = sink

    def warning(self, fmt, *a):
        self.sink.append(fmt % a)

    def info(self, fmt, *a):
        pass

    def debug(self, fmt, *a):
        pass


class DeadModule(types.ModuleType):
    """A module whose every attribute access raises ImportError.

    `from skills import extract_skills, ...` reaches getattr, so this is
    how the probe reproduces the inline import failing. The MESSAGE is
    fixture-chosen and matches what the Go side's nil-dep guard reports:
    the real text CPython produces for a missing module names the module
    and the path, and pinning that would be pinning the probe's own
    mechanism rather than this function's handling of it.
    """

    def __getattr__(self, name):
        raise ImportError("no module")


def tag(adapter):
    if adapter is None:
        return "none"
    return "truthy" if adapter else "falsy"


def to_pairs(v):
    if isinstance(v, dict):
        return [[k, to_pairs(x)] for k, x in v.items()]
    if isinstance(v, list):
        return [to_pairs(x) for x in v]
    return v


def run_one(sc):
    calls, logs = [], []

    def load_skills():
        calls.append({"call": "load_skills"})
        if sc["load_raise"]:
            raise Boom(sc["load_raise"])
        return [Skill(n) for n in sc["existing"]]

    def extract_skills(outcomes, adapter):
        calls.append({"call": "extract_skills",
                      "outcomes": to_pairs(outcomes),
                      "adapter": tag(adapter)})
        if sc["extract_raise"]:
            raise Boom(sc["extract_raise"])
        return [Skill(n) for n in sc["extracted"]]

    def save_skill(skill):
        calls.append({"call": "save_skill", "name": skill.name})
        if sc["save_raise_on"] and skill.name == sc["save_raise_on"]:
            raise Boom("save failed for " + skill.name)

    def synthesize_skill(*, goal, outcome_summary, source_loop_id, adapter,
                         verbose):
        calls.append({"call": "synthesize_skill", "goal": goal,
                      "summary": outcome_summary,
                      "loop_id": source_loop_id, "adapter": tag(adapter),
                      "verbose": verbose})
        if sc["synth_raise"]:
            raise Boom(sc["synth_raise"])

    if sc["missing_skills"]:
        skills_mod = DeadModule("skills")
    else:
        skills_mod = types.ModuleType("skills")
        skills_mod.load_skills = load_skills
        skills_mod.extract_skills = extract_skills
        skills_mod.save_skill = save_skill
    if sc["missing_evolver"]:
        evolver_mod = DeadModule("evolver")
    else:
        evolver_mod = types.ModuleType("evolver")
        evolver_mod.synthesize_skill = synthesize_skill
    sys.modules["skills"] = skills_mod
    sys.modules["evolver"] = evolver_mod

    adapter = {"none": None, "truthy": object(),
               "falsy": FalsyAdapter()}[sc["adapter"]]
    outcomes = [StepOutcome(index=i, text=s["text"], status=s["status"], iteration=0,
                            result=s["result"])
                for i, s in enumerate(sc["steps"])]

    lf.log = Log(logs)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        lf._crystallize_and_synthesize(
            loop_id=sc["loop_id"], goal=sc["goal"], project=sc["project"],
            loop_status=sc["loop_status"], step_outcomes=outcomes,
            adapter=adapter, verbose=sc["verbose"],
            had_no_matching_skill=sc["had_no_matching"])
    return {"name": sc["name"], "calls": calls, "warnings": logs,
            "stderr": err.getvalue()}


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
print(json.dumps([run_one(sc) for sc in spec]))
