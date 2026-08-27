import io, json, logging, os, sys, types
from pathlib import Path

SRC = sys.argv[1]
TMP = sys.argv[2]
sys.path.insert(0, SRC)

import loop_finalize as lf
from loop_types import LoopContext, StepOutcome


class Boom(RuntimeError):
    pass


def raiser(msg):
    def _f(*a, **kw):
        raise Boom(msg)
    return _f


def run_one(sc):
    beh = sc.get("beh") or {}
    calls = []
    logs = []
    stderr = []
    root = Path(TMP) / sc["name"]
    root.mkdir(parents=True, exist_ok=True)

    def rec(_call, **kw):
        # The leading underscore is not decoration: one of the recorded
        # calls has a keyword argument literally named "name".
        calls.append(dict(kw, call=_call))

    # ---- module-level surfaces -------------------------------------
    def w_manifest(**kw):
        rec("plan_manifest", status=kw["status"], elapsed_ms=kw["elapsed_ms"],
            replan=kw["replan_count"], steps=list(kw["planned_steps"]),
            start_ts=kw["start_ts"], project=kw["project"], loop_id=kw["loop_id"])
        if beh.get("manifest_raise"):
            raise Boom(beh["manifest_raise"])

    def w_report(**kw):
        rec("run_report", status=kw["status"], elapsed_ms=kw["elapsed_ms"],
            injections=list(kw["injections"]))
        if beh.get("report_raise"):
            raise Boom(beh["report_raise"])

    def w_loglog(**kw):
        rec("loop_log", status=kw["status"], elapsed_ms=kw["elapsed_ms"],
            stuck=kw["stuck_reason"], nsteps=len(kw["steps"]),
            injections=list(kw["injections"]))
        if beh.get("loglog_raise"):
            raise Boom(beh["loglog_raise"])
        return beh["log_path"]

    def w_index(force=False):
        rec("runs_index", force=force)
        if beh.get("runsindex_raise"):
            raise Boom(beh["runsindex_raise"])

    class Orch:
        def append_decision(self, project, lines):
            rec("append_decision", project=project, lines=list(lines))
            if beh.get("appenddec_raise"):
                raise Boom(beh["appenddec_raise"])

        def write_operator_status(self):
            rec("operator_status")
            if beh.get("opstatus_raise"):
                raise Boom(beh["opstatus_raise"])

    def finalize(**kw):
        rec("finalize_loop", **{k: (list(v) if isinstance(v, list) else v)
                                for k, v in kw.items()
                                if k not in ("adapter", "step_outcomes")})
        if beh.get("finalize_raise"):
            raise Boom(beh["finalize_raise"])

    def cleanup(project, exclude_loop_id=""):
        rec("cleanup_artifacts", project=project, exclude=exclude_loop_id)
        return 0

    lf._write_plan_manifest = w_manifest
    lf._write_run_report = w_report
    lf._write_loop_log = w_loglog
    lf._write_runs_index = w_index
    lf._orch = lambda: Orch()
    lf._finalize_loop = finalize
    lf.cleanup_step_artifacts = cleanup
    lf._project_dir_root = lambda: root / "projroot"

    lf.time = types.SimpleNamespace(monotonic=lambda: float(beh["monotonic"]))

    class FakeDT:
        @staticmethod
        def now(tz=None):
            return types.SimpleNamespace(
                isoformat=lambda: beh["now"])
    lf.datetime = FakeDT

    buf = io.StringIO()
    lf.sys = types.SimpleNamespace(stderr=buf)

    # ---- lazily imported modules -----------------------------------
    memdir = root / "memory"
    memdir.mkdir(parents=True, exist_ok=True)

    def locked_append(path, line):
        rec("locked_append", name=Path(path).name)
        if beh.get("lockedappend_raise"):
            raise Boom(beh["lockedappend_raise"])
        with io.open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def artifact_dir(project, project_root_fn=None):
        if beh.get("artifactdir_raise"):
            raise Boom(beh["artifactdir_raise"])
        d = root / "art" / project / "artifacts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def land_facts(ledger, loop_id=None, project=None, dry_run=False):
        rec("land_facts", loop_id=loop_id, project=project, dry_run=dry_run)
        if beh.get("landfacts_raise"):
            raise Boom(beh["landfacts_raise"])
        lfc = beh["landfacts"]
        return {"anecdotal": lfc[0], "hypotheses": lfc[1]}

    def write_event(name, **kw):
        rec("write_event", name=name, kw=dict(kw))
        if beh.get("writeevent_raise"):
            raise Boom(beh["writeevent_raise"])

    def stamp_outcome(loop_id, verdict, evidence):
        rec("stamp_outcome", loop_id=loop_id, verdict=verdict, evidence=evidence)
        if beh.get("stampoutcome_raise"):
            raise Boom(beh["stampoutcome_raise"])

    def stamp_run(stop_verdict=None, stop_evidence=None, pause_reason=None):
        rec("stamp_run", verdict=stop_verdict, evidence=stop_evidence,
            pause=pause_reason)
        if beh.get("stamprun_raise"):
            raise Boom(beh["stamprun_raise"])

    def merge_back_clone(clone, message=""):
        rec("merge_back_clone", message=message)
        b = beh.get("clone_merge") or {}
        if b.get("raise"):
            raise Boom(b["raise"])
        return types.SimpleNamespace(ok=b.get("ok", True),
                                     detail=b.get("detail", ""),
                                     branch=b.get("branch", ""))

    def cleanup_clone(clone, keep_on_failure=False):
        rec("cleanup_clone", keep=keep_on_failure)
        if beh.get("clone_cleanup_raise"):
            raise Boom(beh["clone_cleanup_raise"])

    def merge_back(wt, message=""):
        rec("merge_back", message=message)
        b = beh.get("wt_merge") or {}
        if b.get("raise"):
            raise Boom(b["raise"])
        return types.SimpleNamespace(ok=b.get("ok", True),
                                     detail=b.get("detail", ""),
                                     branch=b.get("branch", ""))

    def wt_cleanup(wt, keep_on_failure=False):
        rec("wt_cleanup", keep=keep_on_failure)
        if beh.get("wt_cleanup_raise"):
            raise Boom(beh["wt_cleanup_raise"])

    def wt_prune(repo_dir):
        rec("wt_prune", repo_dir=str(repo_dir))
        if beh.get("wt_prune_raise"):
            raise Boom(beh["wt_prune_raise"])

    def clear_running():
        rec("clear_running")
        if beh.get("clear_raise"):
            raise Boom(beh["clear_raise"])

    def post_hb(event_type=None, payload=None):
        rec("post_heartbeat", event_type=event_type, payload=payload)
        if beh.get("hb_raise"):
            raise Boom(beh["hb_raise"])

    fake = {
        "orch_items": types.SimpleNamespace(memory_dir=lambda: memdir),
        "file_lock": types.SimpleNamespace(locked_append=locked_append),
        "observe": types.SimpleNamespace(write_event=write_event),
        "world_facts": types.SimpleNamespace(land_facts=land_facts),
        "runs": types.SimpleNamespace(artifact_dir=artifact_dir,
                                      stamp_run_stop_verdict=stamp_run),
        "memory_ledger": types.SimpleNamespace(
            stamp_outcome_stop_verdict=stamp_outcome),
        "worktree": types.SimpleNamespace(
            merge_back_clone=merge_back_clone, cleanup_clone=cleanup_clone,
            merge_back=merge_back, cleanup=wt_cleanup, prune=wt_prune),
        "interrupt": types.SimpleNamespace(clear_loop_running=clear_running),
        "heartbeat": types.SimpleNamespace(post_heartbeat_event=post_hb),
    }
    if beh.get("memdir_raise"):
        fake["orch_items"] = types.SimpleNamespace(
            memory_dir=raiser(beh["memdir_raise"]))
    for k, v in fake.items():
        sys.modules[k] = v

    # ---- context + arguments ---------------------------------------
    c = sc["ctx"]
    ctx = LoopContext()
    ctx.loop_id = c["loop_id"]
    ctx.project = c["project"]
    ctx.goal = c["goal"]
    ctx.dry_run = c.get("dry_run", False)
    ctx.verbose = c.get("verbose", False)
    ctx.injections = list(c.get("injections") or [])
    ctx.stop_verdict = c.get("stop_verdict", "")
    ctx.stop_evidence = c.get("stop_evidence", "")
    ctx.pause_reason = c.get("pause_reason", "")
    ctx.measurement_class = c.get("measurement_class", "")
    ctx.handle_id = c.get("handle_id", "")
    ctx.defer_learning = c.get("defer_learning", False)
    ctx.defer_maintenance = c.get("defer_maintenance", False)
    ctx.started_at = float(c.get("started_at", 0.0))
    ctx.adapter = None

    class Slot:
        def __init__(self, tag):
            self.tag = tag

        def release(self):
            rec("release_" + self.tag)
            if beh.get(self.tag + "_raise"):
                raise Boom(beh[self.tag + "_raise"])

    ctx.project_slot = Slot("slot") if c.get("has_slot") else None
    ctx.run_lease = Slot("lease") if c.get("has_lease") else None
    cc = c.get("container_clone")
    ctx.container_clone = (types.SimpleNamespace(**cc) if cc else None)
    wt = c.get("run_worktree")
    ctx.run_worktree = (types.SimpleNamespace(**wt) if wt else None)

    i = sc["in"]
    outs = []
    for s in i.get("steps") or []:
        outs.append(StepOutcome(index=s["index"], text=s["text"],
                                status=s["status"], result=s.get("result", ""),
                                iteration=s.get("iteration", 0)))

    pf = i.get("pf")
    pf_obj = None
    if pf:
        pf_obj = types.SimpleNamespace(
            scope=pf["scope"],
            milestone_step_indices=list(pf.get("milestones") or []),
            flags=[types.SimpleNamespace(kind=k) for k in (pf.get("flags") or [])])

    handler = logging.Handler()
    handler.emit = lambda r: logs.append(
        {"level": r.levelname, "msg": r.getMessage()})
    lg = logging.getLogger("maro.loop")
    lg.handlers = [handler]
    lg.setLevel(logging.DEBUG)
    lg.propagate = False

    out = {"name": sc["name"]}
    try:
        res = lf._build_result_and_finalize(
            ctx,
            step_outcomes=outs,
            loop_status=i["loop_status"],
            stuck_reason=i.get("stuck_reason"),
            total_tokens_in=i.get("tokens_in", 0),
            total_tokens_out=i.get("tokens_out", 0),
            interrupts_applied=i.get("interrupts", 0),
            march_of_nines_alert=i.get("march", False),
            pf_review=pf_obj,
            manifest_steps=list(i.get("manifest_steps") or []),
            replan_count=i.get("replan", 0),
            start_ts=i.get("start_ts", "T0"),
            milestone_expanded=set(range(i.get("milestone_expanded", 0))),
            had_no_matching_skill=i.get("had_no_skill", False),
            failure_chain=list(i.get("failure_chain") or []),
            recovery_step_count=i.get("recovery", 0),
            # An ordered list of {k, v}, rebuilt into a dict here:
            # insertion order is what index.json records.
            scratchpad={e["k"]: e["v"] for e in (i.get("scratchpad") or [])},
            scratchpad_lock=DummyLock(),
        )
        out["result"] = {
            "loop_id": res.loop_id, "project": res.project, "goal": res.goal,
            "status": res.status, "nsteps": len(res.steps),
            "interrupts": res.interrupts_applied,
            "injections": list(res.injections),
            "stuck_reason": res.stuck_reason,
            "stop_verdict": res.stop_verdict,
            "stop_evidence": res.stop_evidence,
            "pause_reason": res.pause_reason,
            "tokens_in": res.total_tokens_in, "tokens_out": res.total_tokens_out,
            "elapsed_ms": res.elapsed_ms, "log_path": str(res.log_path),
            "march": res.march_of_nines_alert,
            "had_no_skill": res.had_no_matching_skill,
            "summary": res.summary(),
        }
    except Exception as exc:
        out["error"] = type(exc).__name__ + ": " + str(exc)

    out["calls"] = calls
    out["logs"] = logs
    out["stderr"] = buf.getvalue()
    out["ctx_after"] = {
        "slot": ctx.project_slot is not None,
        "lease": ctx.run_lease is not None,
        "clone": ctx.container_clone is not None,
        "worktree": ctx.run_worktree is not None,
    }
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files[str(p.relative_to(root))] = io.open(
                p, encoding="utf-8").read()
    out["files"] = files
    return out


class DummyLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


scenarios = json.loads(io.open(sys.argv[3], encoding="utf-8").read())
print(json.dumps([run_one(s) for s in scenarios]))
