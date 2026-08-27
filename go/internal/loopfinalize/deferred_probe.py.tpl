import contextlib, dataclasses, io, json, sys, types

SRC = sys.argv[1]
sys.path.insert(0, SRC)

import loop_finalize as lf
import memory_ledger as _ml
import outcome_policy as _op
from loop_types import StepOutcome


class Boom(RuntimeError):
    pass


class PartialModule(types.ModuleType):
    """A module missing SOME of its names.

    Guard PLACEMENT is what this makes falsifiable: `from memory_ledger
    import load_outcome_by_loop_id` inside the step-lesson try fails at
    the TOP of that try, before the row is read, and a stub that can only
    delete a whole module cannot tell that from a failure at the call
    site.

    ImportError("no module") rather than CPython's "cannot import name
    ..." because the port's errImport is a single sentinel; the shape
    under test is which handler catches it, not the interpreter's wording.
    """

    def __getattr__(self, name):
        # `from X import y` runs _handle_fromlist, which probes __path__
        # through hasattr — and hasattr swallows only AttributeError.
        # Raising ImportError for a dunder fails EVERY import from this
        # module rather than the dropped names.
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


class Result:
    """loop_result. Every read of it is getattr-with-default, so a field
    the fixture omits is a real case, not a broken fixture."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class Skill:
    def __init__(self, name):
        self.name = name


def tag(adapter):
    if adapter is None:
        return "none"
    return "truthy" if adapter else "falsy"


def raiser(msg):
    if msg:
        raise Boom(msg)


def make_row(pairs):
    """A REAL dataclass: the step-lesson block calls dataclasses.asdict on
    the row, and asdict rejects anything else. Field ORDER is the
    fixture's, and the field SET is too — `"success_class" in outcome`
    inside is_learnable_outcome asks whether the field was declared, so a
    row that omits it is a different row, not the same row with a None."""
    Row = dataclasses.make_dataclass(
        "Row", [(k, object) for k, _ in pairs])
    return Row(**{k: v for k, v in pairs})


ORIG_MINT = lf._mint_run_risks_to_project


def run_one(sc):
    calls, logs = [], []

    def rec(_call, /, **kw):
        # Positional-only: a recorded kwarg named `name` would otherwise
        # collide with the recorder's own parameter and raise inside the
        # very handler being observed.
        calls.append(dict(kw, call=_call))

    def extract_deferred_lessons(loop_id, adapter=None, dry_run=False):
        rec("extract_deferred_lessons", loop_id=loop_id,
            adapter=tag(adapter), dry_run=dry_run)
        raiser(sc["deferred_raise"].get(loop_id, ""))

    def extract_step_lessons(goal, steps, task_type="", adapter=None,
                             loop_id="", dry_run=False):
        rec("extract_step_lessons", goal=goal, nsteps=len(steps),
            task_type=task_type, adapter=tag(adapter), loop_id=loop_id,
            dry_run=dry_run)
        raiser(sc["step_lessons_raise"])

    def load_outcome_by_loop_id(loop_id):
        rec("load_outcome_by_loop_id", loop_id=loop_id)
        raiser(sc["load_raise"])
        if sc["row"] is None:
            return None
        return make_row([(p[0], p[1]) for p in sc["row"]])

    def mint(project, loop_id):
        rec("_mint_run_risks_to_project", project=project, loop_id=loop_id)
        return 0

    def load_skills():
        rec("load_skills")
        return []

    def extract_skills(outcomes, adapter):
        rec("extract_skills", n=len(outcomes), adapter=tag(adapter),
            goal=outcomes[0]["goal"], status=outcomes[0]["status"],
            project=outcomes[0]["project"])
        return [Skill(n) for n in sc["extracted"]]

    def save_skill(skill):
        rec("save_skill", skill=skill.name)

    def synthesize_skill(*, goal, outcome_summary, source_loop_id, adapter,
                         verbose):
        rec("synthesize_skill", goal=goal, summary=outcome_summary,
            loop_id=source_loop_id, adapter=tag(adapter), verbose=verbose)

    drop = set(sc["drop_names"])

    memory = PartialModule("memory")
    if "extract_deferred_lessons" not in drop:
        memory.extract_deferred_lessons = extract_deferred_lessons
    if "extract_step_lessons" not in drop:
        memory.extract_step_lessons = extract_step_lessons
    sys.modules["memory"] = memory

    ledger = PartialModule("memory_ledger")
    if "load_outcome_by_loop_id" not in drop:
        ledger.load_outcome_by_loop_id = load_outcome_by_loop_id
    # verdict_trust / VERDICT_TRUST_FULL / is_learnable_outcome stay REAL
    # and are never dropped: in Go they are package calls, not injectable
    # deps, so an absence fixture would be testing the probe's reach
    # rather than the port's. The seam they sit on is covered by driving
    # the real implementations with rows that exercise every branch.
    ledger.verdict_trust = _ml.verdict_trust
    ledger.VERDICT_TRUST_FULL = _ml.VERDICT_TRUST_FULL
    sys.modules["memory_ledger"] = ledger

    policy = PartialModule("outcome_policy")
    policy.is_learnable_outcome = _op.is_learnable_outcome
    sys.modules["outcome_policy"] = policy

    skills = types.ModuleType("skills")
    skills.load_skills = load_skills
    skills.extract_skills = extract_skills
    skills.save_skill = save_skill
    sys.modules["skills"] = skills

    evolver = types.ModuleType("evolver")
    evolver.synthesize_skill = synthesize_skill
    sys.modules["evolver"] = evolver

    lf.log = Log(logs)
    lf._mint_run_risks_to_project = mint

    adapter = {"none": None, "truthy": object()}[sc["adapter"]]
    steps = [StepOutcome(index=s["index"], text=s["text"],
                         status=s["status"], iteration=0, result=s["result"])
             for s in sc["steps"]]

    fields = {"goal": sc["goal"], "status": sc["status"], "steps": steps,
              "had_no_matching_skill": sc["had_no_matching"]}
    if not sc["no_loop_id"]:
        fields["loop_id"] = sc["loop_id"]
    if not sc["no_result_project"]:
        fields["project"] = sc["result_project"]

    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            lf.finalize_deferred_learning(
                Result(**fields), adapter=adapter, project=sc["project"],
                dry_run=sc["dry_run"], verbose=sc["verbose"],
                extra_loop_ids=sc["extra_loop_ids"],
                skip_loop_ids=sc["skip_loop_ids"],
                unstamped_loop_ids=sc["unstamped_loop_ids"])
    finally:
        lf._mint_run_risks_to_project = ORIG_MINT

    return {"name": sc["name"], "calls": calls, "logs": logs,
            "stderr": err.getvalue()}


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
print(json.dumps([run_one(sc) for sc in spec]))
