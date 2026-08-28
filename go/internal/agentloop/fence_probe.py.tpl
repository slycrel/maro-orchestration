import builtins, contextlib, io, json, sys, types

SRC = sys.argv[1]
sys.path.insert(0, SRC)

import agent_loop as al


class ReachedDecompose(Exception):
    """Raised in place of _decompose_goal.

    The fence is the FIRST thing run_agent_loop does after loop-id
    scoping, and there is no seam between it and the rest of the run. So
    the probe drives the real function with _initialize_loop faked and
    _decompose_goal raising: everything recorded before this exception is
    the fence, and the exception itself is the record's "the fence came
    up" marker.
    """


class PartialModule(types.ModuleType):
    """A module missing SOME of its names — for `from X import y`.

    See L59: names imported together fail together, at the top of their
    own try, and a stub that can only delete whole modules cannot say
    which handler caught what.
    """

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        raise ImportError("no module")


class Blocker:
    """A meta-path finder that makes named modules genuinely unimportable.

    A stub whose every attribute raises is NOT the same state. `import
    container_exec as _ce` against such a stub SUCCEEDS — it binds the
    module object, and the failure happens later, at the attribute, with
    `_ce` bound. A module that is actually missing raises at the import
    statement and leaves `_ce` unbound, which is what the refusal's
    neutralisation lambdas close over.

    The message is CPython's own, exactly: an operator searches for it.
    """

    def __init__(self, names):
        self.names = set(names)

    def find_spec(self, name, path=None, target=None):
        if name in self.names:
            raise ModuleNotFoundError("No module named %r" % name,
                                      name=name)
        return None


class P:
    """A stand-in for pathlib.Path.

    The fence's paths are POLICY, not storage: the run's cwd, the rw
    mount set, the scratch dir. Driving them through the real filesystem
    would make the record depend on a temp dir that differs between the
    two sides, and would put a mkdir in the middle of a safety decision.
    So the path is a value that records what was asked of it.
    """

    def __init__(self, s, sink, raiser_for, key="mkdir"):
        self.s = str(s)
        self._sink = sink
        self._raiser = raiser_for
        # The fence dir and the workspace scratch dir are two different
        # mkdirs in two different handlers, so they get two raise keys.
        self._key = key

    def __truediv__(self, other):
        return P(self.s.rstrip("/") + "/" + str(other), self._sink,
                 self._raiser, self._key)

    def __str__(self):
        return self.s

    def mkdir(self, parents=False, exist_ok=False):
        self._sink("mkdir", path=self.s, parents=parents,
                   exist_ok=exist_ok)
        self._raiser(self._key)


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


class Releaser:
    def __init__(self, label, sink, raiser_for):
        self.label = label
        self._sink = sink
        self._raiser = raiser_for

    def release(self):
        self._sink("release", who=self.label)
        self._raiser("release_" + self.label)


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Ctx:
    """The slice of LoopContext the fence reads and writes."""

    def __init__(self, sc, sink, raiser_for):
        self.loop_id = sc["loop_id"]
        self.goal = sc["goal"]
        self.project = sc["ctx_project"]
        self.start_ts = "2026-01-01T00:00:00Z"
        self.started_at = 100.0
        self.adapter = None
        self.interrupt_queue = None
        self.perm_ctx = None
        self.measurement_class = ""
        self.handle_id = ""
        self.defer_learning = False
        self.injections = []
        self.run_worktree = None
        if sc["run_worktree"]:
            self.run_worktree = Obj(
                path=P(sc["run_worktree"]["path"], sink, raiser_for),
                repo_dir=sc["run_worktree"]["repo_dir"])
        self.container_clone = None
        if sc["preset_clone"]:
            self.container_clone = Obj(path=sc["preset_clone"]["path"],
                                       branch=sc["preset_clone"]["branch"])
        self.project_slot = (Releaser("slot", sink, raiser_for)
                             if sc["has_project_slot"] else None)
        self.run_lease = (Releaser("lease", sink, raiser_for)
                          if sc["has_run_lease"] else None)
        self.phases = []

    def set_phase(self, phase):
        self.phases.append(str(getattr(phase, "value", phase)))


def run_one(sc):
    calls, logs = [], []

    def rec(_call, /, **kw):
        calls.append(dict(kw, call=_call))

    counts = {}

    def raiser(key):
        # [class, message] raises on EVERY call; an optional third element
        # is the 1-based call index to raise on, which is the only way to
        # reach a setter's SECOND use (the resets call two of them once
        # before the fence body ever gets there).
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
        # Always built and always populated. A DEAD module is swapped in
        # at install() time instead of here, because assigning attributes
        # onto a DeadModule defeats its __getattr__ — __getattr__ fires
        # only for names that are MISSING, so a populated dead module is
        # a live one wearing the wrong class.
        return PartialModule(name) if partial else types.ModuleType(name)

    def install(name, m):
        if name in dead:
            sys.modules.pop(name, None)
            return
        sys.modules[name] = m

    # --- llm: a FROM-import of two names, plus the tier constants -------
    llm = mod("llm")
    llm.MODEL_CHEAP, llm.MODEL_MID, llm.MODEL_POWER = "cheap", "mid", "power"
    if "set_default_subprocess_cwd" not in drop:
        def set_default_subprocess_cwd(d):
            rec("set_default_subprocess_cwd", dir=d)
            raiser("set_default_subprocess_cwd")
        llm.set_default_subprocess_cwd = set_default_subprocess_cwd
    if "set_default_container_rw_roots" not in drop:
        def set_default_container_rw_roots(roots):
            rec("set_default_container_rw_roots", roots=list(roots))
            raiser("set_default_container_rw_roots")
        llm.set_default_container_rw_roots = set_default_container_rw_roots
    install("llm", llm)

    # --- container_exec: a MODULE import, names reached by attribute ----
    ce = mod("container_exec", partial=False)
    if "set_container_suppressed" not in drop:
        def set_container_suppressed(v):
            rec("set_container_suppressed", v=v)
            raiser("set_container_suppressed")
        ce.set_container_suppressed = set_container_suppressed
    if "set_introspection_run" not in drop:
        def set_introspection_run(v):
            rec("set_introspection_run", v=v)
            raiser("set_introspection_run")
        ce.set_introspection_run = set_introspection_run
    if "container_configured" not in drop:
        def container_configured():
            rec("container_configured")
            raiser("container_configured")
            return sc["container_configured"]
        ce.container_configured = container_configured
    install("container_exec", ce)

    # --- worktree: also a MODULE import, imported TWICE -----------------
    wt = mod("worktree", partial=False)
    if "is_git_repo" not in drop:
        def is_git_repo(d):
            rec("is_git_repo", dir=str(d))
            raiser("is_git_repo")
            return sc["is_git_repo"]
        wt.is_git_repo = is_git_repo
    if "provision_clone" not in drop:
        def provision_clone(d, kind, loop_id=""):
            rec("provision_clone", dir=str(d), kind=kind, loop_id=loop_id)
            raiser("provision_clone")
            if sc["clone"] is None:
                return None
            return Obj(path=sc["clone"]["path"],
                       branch=sc["clone"]["branch"])
        wt.provision_clone = provision_clone
    if "cleanup_clone" not in drop:
        def cleanup_clone(c):
            rec("cleanup_clone", path=c.path)
            raiser("cleanup_clone")
        wt.cleanup_clone = cleanup_clone
    if "cleanup" not in drop:
        def cleanup(w):
            rec("cleanup", path=str(w.path))
            raiser("cleanup")
        wt.cleanup = cleanup
    if "prune" not in drop:
        def prune(repo_dir):
            rec("prune", repo_dir=repo_dir)
            raiser("prune")
        wt.prune = prune
    install("worktree", wt)

    # --- artifact_check / config: two FROM-imports in one try -----------
    ac = mod("artifact_check")
    if "goal_declared_roots" not in drop:
        def goal_declared_roots(goal):
            rec("goal_declared_roots", goal=goal)
            raiser("goal_declared_roots")
            return sc["declared_roots"]
        ac.goal_declared_roots = goal_declared_roots
    install("artifact_check", ac)

    cfg = mod("config")
    if "config_get" not in drop:
        def cfg_get(key, default=None):
            rec("config_get", key=key, default=default)
            raiser("config_get")
            return sc["write_fence_allow"]
        cfg.get = cfg_get
    if "workspace_root" not in drop:
        def workspace_root():
            rec("workspace_root")
            raiser("workspace_root")
            return P(sc["workspace_root"], rec, raiser, "mkdir_ws")
        cfg.workspace_root = workspace_root
    install("config", cfg)

    intr = mod("interrupt")
    if "clear_loop_running" not in drop:
        def clear_loop_running():
            rec("clear_loop_running")
            raiser("clear_loop_running")
        intr.clear_loop_running = clear_loop_running
    install("interrupt", intr)

    obs = mod("observe")
    if "write_event" not in drop:
        def write_event(event, **kw):
            rec("write_event", event=event, **kw)
            raiser("write_event")
        obs.write_event = write_event
    install("observe", obs)

    runs = mod("runs")
    if "stamp_run_stop_verdict" not in drop:
        def stamp_run_stop_verdict(stop_verdict="", stop_evidence=""):
            rec("stamp_run_stop_verdict", stop_verdict=stop_verdict,
                stop_evidence=stop_evidence)
            raiser("stamp_run_stop_verdict")
        runs.stamp_run_stop_verdict = stamp_run_stop_verdict
    install("runs", runs)

    ctx = Ctx(sc, rec, raiser)
    al.log = Log(logs)
    al._initialize_loop = lambda *a, **k: (ctx, None)
    al._project_dir_root = lambda: (rec("project_dir_root"),
                                    P(sc["project_dir_root"], rec,
                                      raiser))[1]
    al._goal_to_slug = lambda g: (rec("goal_to_slug", goal=g),
                                  sc["goal_slug"])[1]

    def decompose(*a, **k):
        raise ReachedDecompose()
    al._decompose_goal = decompose

    class FakeTime:
        # int((monotonic() - started_at) * 1000) with started_at = 100.0.
        @staticmethod
        def monotonic():
            return 100.25
    al.time = FakeTime

    blocker = Blocker(dead)
    sys.meta_path.insert(0, blocker)

    result = None
    reached = False
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            r = al.run_agent_loop(
                sc["goal"], project=sc["project"],
                introspection_access=sc["introspection_access"])
        result = {"loop_id": r.loop_id, "goal": r.goal, "project": r.project,
                  "status": r.status, "steps": len(r.steps),
                  "stuck_reason": r.stuck_reason,
                  "stop_verdict": r.stop_verdict,
                  "stop_evidence": r.stop_evidence,
                  "elapsed_ms": r.elapsed_ms}
    except ReachedDecompose:
        reached = True
    finally:
        sys.meta_path.remove(blocker)

    return {"name": sc["name"], "calls": calls, "logs": logs,
            "reached_decompose": reached, "result": result,
            "phases": ctx.phases,
            "ctx_clone": (None if ctx.container_clone is None
                          else ctx.container_clone.path),
            "ctx_worktree": (None if ctx.run_worktree is None
                             else str(ctx.run_worktree.path)),
            "ctx_slot": ctx.project_slot is not None,
            "ctx_lease": ctx.run_lease is not None}


spec = json.loads(io.open(sys.argv[2], encoding="utf-8").read())
print(json.dumps([run_one(sc) for sc in spec]))
