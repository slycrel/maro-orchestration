package agentloop

// The execution fence: run_agent_loop's first act after loop_id scoping
// (`src/agent_loop.py:245-430`).
//
// It binds the run-scoped default cwd so EVERY agentic subprocess writes
// in-workspace instead of inheriting Maro's launch cwd, decides whether
// the run works in a scratch clone of its own repo, and computes the
// writable-root set a containerised step is allowed.
//
// Almost every line of it is a SAFETY decision, and the order is the
// specification:
//
//   - the three run-scoped resets happen FIRST, before anything can
//     raise, so a resumed run in the same process never inherits a prior
//     run's suppression flag, rw roots or introspection grant;
//   - a clone that cannot be provisioned SUPPRESSES containerisation
//     rather than proceeding, because the alternative is mounting the
//     live repo rw;
//   - a setup that blows up mid-way suppresses too, on the same
//     reasoning;
//   - and a fence that fails at all REFUSES the loop before decomposition,
//     unwinding the clone, the worktree, the slot and the lease, and
//     neutralising each policy setter one at a time so one failure does
//     not skip the rest.
//
// Nothing here is a goal verdict. The refusal stamps
// `external-interrupt`, because the goal was never attempted.

import (
	"fmt"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// FenceRefusalStopVerdict is what a refused fence stamps. It is NOT a
// goal verdict: the stop-path survey found "stuck" alone read downstream
// as goal failure, and the goal here was never attempted.
const FenceRefusalStopVerdict = "external-interrupt"

// Clone is worktree.provision_clone's return. Only Path and Branch are
// read here; Payload carries the rest so the port is not claiming this
// function depends on fields it never touches.
type Clone struct {
	Path    string
	Branch  string
	Payload any
}

// Worktree is ctx.run_worktree. RepoDir is read only to prune it.
type Worktree struct {
	Path    string
	RepoDir string
	Payload any
}

// Releaser is ctx.project_slot / ctx.run_lease: both are cleared by
// calling .release() and then dropping the reference, and the port keeps
// them as one type because the fence treats them identically apart from
// the message it logs.
type Releaser interface {
	Release() error
}

// FenceCtx is the slice of LoopContext the fence reads AND writes. The
// writes are the point: a refusal that forgot to clear ContainerClone
// would leave a scratch clone on disk with nothing pointing at it.
type FenceCtx struct {
	LoopID  string
	Goal    string
	Project string

	// RunWorktree is busy_policy=worktree's isolated tree. When set, the
	// whole run works there and loop_finalize merges back.
	RunWorktree *Worktree
	// ContainerClone is SET by this function when a self-dev run gets a
	// scratch clone, and CLEARED by the refusal path.
	ContainerClone *Clone

	ProjectSlot Releaser
	RunLease    Releaser
}

// FenceArgs is what the fence reads from run_agent_loop's own parameters.
//
// The `project` keyword is NOT one of them. Twenty lines above the fence
// run_agent_loop rebinds `project = ctx.project`, so by the time
// `project or _goal_to_slug(ctx.goal)` runs, the caller's argument has
// been overwritten by the one _initialize_loop resolved. The port read
// the keyword and picked a different fence dir; the fixture that caught
// it passes two different values and is named for the one that wins.
type FenceArgs struct {
	// IntrospectionAccess is passed through `bool(...)`, so the port takes
	// the bool directly.
	IntrospectionAccess bool
}

// ContainerExecMod is `import container_exec as _ce`. It is a MODULE
// import, not a from-import, so a missing module fails at the import
// statement (nil struct pointer) and a missing NAME fails at the
// attribute access further down (nil field) — two different places, two
// different failures, and both are reachable.
type ContainerExecMod struct {
	SetContainerSuppressed func(bool) error
	SetIntrospectionRun    func(bool) error
	ContainerConfigured    func() (bool, error)
}

// WorktreeMod is `import worktree as _wt`. It is imported TWICE — once
// inside the clone block and once inside the refusal's cleanup — so a
// missing worktree module is caught by two different handlers with two
// different messages.
type WorktreeMod struct {
	IsGitRepo      func(dir string) (bool, error)
	ProvisionClone func(dir, kind, loopID string) (*Clone, error)
	CleanupClone   func(*Clone) error
	Cleanup        func(*Worktree) error
	Prune          func(repoDir string) error
}

// FenceDeps is every module the fence reaches. A nil function field is
// the state "this name could not be imported"; a nil MODULE pointer is
// "this module could not be imported". The distinction is not cosmetic —
// see ContainerExecMod.
type FenceDeps struct {
	// SetDefaultSubprocessCwd and SetDefaultContainerRWRoots arrive on ONE
	// import statement (`from llm import ...`), so losing either fails
	// BOTH, at the top of the try, before any reset has run (L59).
	//
	// SetDefaultSubprocessCwd takes a *string because the refusal calls it
	// with None and the setup calls it with a path, and "no cwd" and "the
	// empty cwd" are not the same instruction.
	SetDefaultSubprocessCwd    func(dir *string) error
	SetDefaultContainerRWRoots func(roots []string) error

	ContainerExec *ContainerExecMod
	Worktree      *WorktreeMod

	// AbsentModules names the modules that cannot be imported AT ALL, as
	// opposed to modules that are present but missing a name.
	//
	// It exists because the two failures are the same handler and a
	// different MESSAGE: a from-import of a missing name raises
	// ImportError, and a from-import of a missing MODULE raises
	// ModuleNotFoundError with CPython's own "No module named 'x'". Both
	// texts reach the operator through a warning. The module-IMPORTS
	// above do not need this — a nil struct pointer already says the
	// module is gone — so only the from-import modules appear here:
	// artifact_check, config, interrupt, observe, runs.
	AbsentModules map[string]bool

	// GoalDeclaredRoots and ConfigGet arrive on two SEPARATE import
	// statements inside the same try, so their failures are
	// indistinguishable in the record — one guard covers both.
	GoalDeclaredRoots func(goal string) ([]string, error)
	ConfigGet         func(key string, def any) (any, error)

	// WorkspaceRoot is `from config import workspace_root`, a THIRD
	// config import in its own try, after the fence.
	WorkspaceRoot func() (string, error)

	ProjectDirRoot func() string
	GoalToSlug     func(goal string) string
	// MkdirParents is `Path.mkdir(parents=True, exist_ok=True)`.
	MkdirParents func(path string) error
	// Realpath is os.path.realpath, which does NOT raise on a missing
	// path — see pypath.Realpath.
	Realpath func(path string) string
	// ExpandUser is os.path.expanduser.
	ExpandUser func(path string) string

	ClearLoopRunning    func() error
	WriteEvent          func(event string, kv pyval.Obj) error
	StampRunStopVerdict func(verdict, evidence string) error

	// ElapsedMS is `int((time.monotonic() - ctx.started_at) * 1000)`,
	// truncating toward zero.
	ElapsedMS func() int

	Info  func(string)
	Warn  func(string)
	Error func(string)
	Debug func(string)
}

func (d FenceDeps) info(format string, a ...any) {
	if d.Info != nil {
		d.Info(fmt.Sprintf(format, a...))
	}
}

func (d FenceDeps) warnf(format string, a ...any) {
	if d.Warn != nil {
		d.Warn(fmt.Sprintf(format, a...))
	}
}

func (d FenceDeps) errorf(format string, a ...any) {
	if d.Error != nil {
		d.Error(fmt.Sprintf(format, a...))
	}
}

// errImportFence is the ImportError a missing NAME stands in for. The
// CLASS matters and not only the message: the refusal line interpolates
// `type(exc).__name__`, so an ImportError, a ModuleNotFoundError and an
// AttributeError produce three different operator-visible strings.
var errImportFence = &pyval.PyErr{Class: "ImportError", Msg: "no module"}

// moduleNotFound is what a missing MODULE raises. Unlike the sentinel
// above, this text is CPython's own and it is exact: a module that is not
// on the path fails with a message the operator will search for.
func moduleNotFound(name string) error {
	return &pyval.PyErr{Class: "ModuleNotFoundError",
		Msg: "No module named '" + name + "'"}
}

// importFailure is what `from <module> import <name>` raises when the
// name is not available: ModuleNotFoundError when the module itself is
// gone, ImportError when only the name is.
func (d FenceDeps) importFailure(module string) error {
	if d.AbsentModules[module] {
		return moduleNotFound(module)
	}
	return errImportFence
}

// attrErr is what a MODULE import raises when the module arrived but the
// name is not on it. `import container_exec as _ce` and `import worktree
// as _wt` both bind the module and reach for names later, so a missing
// name fails at the ATTRIBUTE — one statement lower than a from-import
// would, and sometimes inside a different handler.
func attrErr(module, name string) error {
	return &pyval.PyErr{Class: "AttributeError",
		Msg: "module '" + module + "' has no attribute '" + name + "'"}
}

func ceAttr(name string) error { return attrErr("container_exec", name) }
func wtAttr(name string) error { return attrErr("worktree", name) }

// excName is `type(exc).__name__`.
//
// Errors that cross a fence dep carry their Python class as a
// *pyval.PyErr. Anything else is reported as "Exception", which is what
// a bare `raise Exception(...)` would give — production wrappers must
// classify, because this name reaches the operator inside stuck_reason.
func excName(err error) string {
	var pe *pyval.PyErr
	if ok := asPyErr(err, &pe); ok {
		return pe.Class
	}
	return "Exception"
}

func asPyErr(err error, out **pyval.PyErr) bool {
	for err != nil {
		if pe, ok := err.(*pyval.PyErr); ok {
			*out = pe
			return true
		}
		u, ok := err.(interface{ Unwrap() error })
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}

// unboundLocal is the NameError CPython raises when a lambda closes over
// a name whose binding never happened — which is exactly the state the
// refusal's neutralisation loop is in when the `from llm import ...` at
// the top of the try is what failed.
//
// The wording is CPython 3.11+ ("cannot access free variable ... in
// enclosing scope"); 3.10 and earlier said "free variable ... referenced
// before assignment". It is reproduced rather than normalised because it
// is the line an operator reads during a double fault, but it is a
// VERSION-dependent string and the differential is the only thing keeping
// it honest (L47).
func unboundLocal(name string) error {
	return &pyval.PyErr{Class: "NameError", Msg: "cannot access free " +
		"variable '" + name + "' where it is not associated with a " +
		"value in enclosing scope"}
}

// SetUpExecutionFence returns nil when the fence is up, and the refusal
// LoopResult when it is not. A refusal is a RETURN from run_agent_loop —
// nothing agentic has run, so there is nothing to finalize.
func SetUpExecutionFence(ctx *FenceCtx, a FenceArgs,
	d FenceDeps) *looptypes.LoopResult {
	var bound fenceBindings
	if err := fenceSetup(ctx, a, d, &bound); err != nil {
		return fenceRefusal(ctx, d, bound, err)
	}
	scratchDir(d)
	return nil
}

// fenceBindings is which of the try's own imports actually BOUND before
// it failed.
//
// This is not bookkeeping for its own sake. The refusal's neutralisation
// lambdas close over those names, so a fence that died AT the
// `from llm import ...` leaves all four of them referring to nothing, and
// each one reports a NameError instead of doing its job. A port that
// modelled the deps as always-present would silently neutralise a policy
// Python could not reach.
type fenceBindings struct {
	// cwd and rw are the two names of `from llm import
	// set_default_subprocess_cwd, set_default_container_rw_roots`.
	//
	// TWO flags, not one, and the differential is why. CPython compiles a
	// multi-name from-import into one IMPORT_FROM/STORE_FAST pair PER
	// NAME, in source order — so losing the SECOND name still binds the
	// first. The statement fails either way, but the refusal below can
	// then neutralise the cwd and not the rw policy, and that asymmetry
	// is visible in the log. (L59 says names imported together fail
	// together; this is the other half of it — the names BEFORE the
	// failing one are bound.)
	cwd bool
	rw  bool
	// ce is `import container_exec as _ce`, which runs AFTER the llm
	// import and therefore cannot be bound when that one failed.
	ce bool
}

// fenceSetup is the body of the outer try. Every error it returns lands
// in the refusal.
func fenceSetup(ctx *FenceCtx, a FenceArgs, d FenceDeps,
	b *fenceBindings) error {
	// `from llm import set_default_subprocess_cwd,
	// set_default_container_rw_roots` — one statement, two names, bound
	// one at a time in this order.
	if d.SetDefaultSubprocessCwd == nil {
		return errImportFence
	}
	b.cwd = true
	if d.SetDefaultContainerRWRoots == nil {
		return errImportFence
	}
	b.rw = true
	// `import container_exec as _ce` — the MODULE.
	if d.ContainerExec == nil {
		return moduleNotFound("container_exec")
	}
	b.ce = true
	ce := d.ContainerExec

	// The three resets run FIRST, before anything can raise. A resumed or
	// sequential run in the same process must never inherit a prior run's
	// suppression flag, authorized rw roots, or introspection grant
	// (adversarial review 2026-07-13, finding D).
	if ce.SetContainerSuppressed == nil {
		return ceAttr("set_container_suppressed")
	}
	if err := ce.SetContainerSuppressed(false); err != nil {
		return err
	}
	if err := d.SetDefaultContainerRWRoots([]string{}); err != nil {
		return err
	}
	if ce.SetIntrospectionRun == nil {
		return ceAttr("set_introspection_run")
	}
	if err := ce.SetIntrospectionRun(a.IntrospectionAccess); err != nil {
		return err
	}

	fenceDir := ""
	if ctx.RunWorktree != nil {
		// busy_policy=worktree: the whole run works in its isolated
		// worktree and loop_finalize merges back into the project dir.
		fenceDir = ctx.RunWorktree.Path
	} else {
		// A project-less run is NOT exempt (BACKLOG #1, third repro:
		// dispatched goals arrived with project=None and the whole run
		// executed with the inherited launch cwd, leaking relative writes
		// into the repo root). The fallback is the goal-slug project dir,
		// the same identity the scope pass derives, and it is CREATED,
		// because Popen raises on a missing cwd.
		// `_project_dir_root() / (project or _goal_to_slug(ctx.goal))` —
		// the LEFT operand is evaluated first, and the slug is only
		// derived when the project is blank.
		root := d.ProjectDirRoot()
		slug := ctx.Project
		if slug == "" {
			slug = d.GoalToSlug(ctx.Goal)
		}
		fenceDir = joinPath(root, slug)
	}
	if err := d.MkdirParents(fenceDir); err != nil {
		return err
	}

	liveRepo := ""
	if newDir, err := containerClone(ctx, d, fenceDir, &liveRepo); err != nil {
		return err
	} else {
		fenceDir = newDir
	}

	// Unprotected on purpose: a cwd that cannot be bound is a fence
	// failure, not a degradation.
	if err := d.SetDefaultSubprocessCwd(&fenceDir); err != nil {
		return err
	}

	rwRoots, err := rwRootsFor(ctx, d, liveRepo)
	if err != nil {
		// Root discovery is optional enrichment. An empty list leaves
		// only the already-bound cwd writable, which is the safe
		// fallback.
		d.warnf("container rw-roots discovery failed; using cwd only: %s",
			err)
		rwRoots = []string{}
	}
	// Binding the safe fallback is MANDATORY: swallowing a setter failure
	// here could retain a previous run's writable-root policy.
	return d.SetDefaultContainerRWRoots(rwRoots)
}

// containerClone is C3 (CONTAINER_EXECUTOR_DESIGN §4): when this run is
// CONFIGURED to containerise and the fence dir is a git repo, the live
// repo is never mounted rw — it is cloned into a throwaway scratch, the
// copy is worked, and finalize merges back host-side.
//
// The decision is on config INTENT, not a live docker probe, so it cannot
// race the daemon between here and an executor call. Off by default, so a
// non-container run is byte-identical to before.
//
// It returns the fence dir to use, and sets liveRepo when there is one.
func containerClone(ctx *FenceCtx, d FenceDeps, fenceDir string,
	liveRepo *string) (string, error) {
	ce := d.ContainerExec
	intended := false
	err := func() error {
		if ce.ContainerConfigured == nil {
			return ceAttr("container_configured")
		}
		var err error
		// `_container_intended` is assigned from the call, so a call that
		// RAISES leaves it false — and the handler below then does not
		// suppress. Two failures one line apart, two different postures.
		intended, err = ce.ContainerConfigured()
		if err != nil {
			return err
		}
		if ctx.ContainerClone != nil || !intended {
			return nil
		}
		if d.Worktree == nil {
			return moduleNotFound("worktree")
		}
		if d.Worktree.IsGitRepo == nil {
			return wtAttr("is_git_repo")
		}
		isRepo, err := d.Worktree.IsGitRepo(fenceDir)
		if err != nil {
			return err
		}
		if !isRepo {
			return nil
		}
		*liveRepo = fenceDir
		var clone *Clone
		// provision_clone has its OWN handler: a provisioning failure is
		// a suppression, not a fence failure — and that includes the
		// module not having the NAME, because the attribute access is
		// inside this try and not at the import above it.
		var c *Clone
		var perr error
		if d.Worktree.ProvisionClone == nil {
			perr = wtAttr("provision_clone")
		} else {
			c, perr = d.Worktree.ProvisionClone(fenceDir, "container",
				ctx.LoopID)
		}
		if perr != nil {
			d.warnf("container scratch-clone provision error: %s", perr)
		} else {
			clone = c
		}
		if clone != nil {
			ctx.ContainerClone = clone
			fenceDir = clone.Path
			d.info("container self-dev: working in scratch clone %s "+
				"(branch %s)", clone.Path, clone.Branch)
			return nil
		}
		// FAIL CLOSED. If the clone cannot be made the live repo is never
		// mounted rw (adversarial review 2026-07-13, findings A/M3/S2/A1).
		if err := ce.SetContainerSuppressed(true); err != nil {
			return err
		}
		// EQUIVALENT MUTANT (kept, marked `equivalent`): the row that
		// prints fenceDir instead of *liveRepo. This branch runs only
		// when the clone was NOT provisioned, and the two names are the
		// same string until a clone moves the fence dir.
		d.warnf("container self-dev: could not provision a scratch clone "+
			"of %s — executor steps run on the HOST under the fence, NOT "+
			"containerized (the live repo is never mounted rw)", *liveRepo)
		return nil
	}()
	if err != nil {
		d.warnf("container scratch-clone setup skipped: %s", err)
		// Setup itself blew up while INTENDING to containerize a git repo
		// → fail closed rather than risk mounting the live repo rw. The
		// clone test is re-read here, not remembered: a clone that was
		// assigned before the failure is not suppressed.
		// EQUIVALENT MUTANT (kept, marked `equivalent`): dropping the
		// clone half of this test. Nothing between `ctx.container_clone =
		// _clone` and the end of the try can raise — the only statement
		// left is a log call, and the logging module swallows handler
		// errors rather than propagating them — so reaching this handler
		// with intended=True implies no clone was recorded.
		if intended && ctx.ContainerClone == nil {
			// Unprotected: this one escapes to the outer handler and
			// refuses the loop.
			if serr := ce.SetContainerSuppressed(true); serr != nil {
				return fenceDir, serr
			}
		}
	}
	return fenceDir, nil
}

// rwRootsFor is the container fence's writable mount set (C3): the goal's
// declared roots plus validate.write_fence_allow. The cwd is always rw;
// host /tmp and the workspace root are hard-excluded by build_mount_map,
// and the live repo of a self-dev run is dropped HERE so a goal that
// names its own repo path cannot re-introduce it as a rw mount
// (adversarial review 2026-07-13, finding B).
func rwRootsFor(ctx *FenceCtx, d FenceDeps, liveRepo string) ([]string,
	error) {
	// Two import statements at the top of one try, in this order. A
	// failure of either is the same log LINE and not the same message.
	if d.GoalDeclaredRoots == nil {
		return nil, d.importFailure("artifact_check")
	}
	if d.ConfigGet == nil {
		return nil, d.importFailure("config")
	}
	roots, err := d.GoalDeclaredRoots(ctx.Goal)
	if err != nil {
		// `_rw_roots = list(_gdr(...))` — a raise here leaves the name
		// UNBOUND, so there is nothing partial to carry out.
		return nil, err
	}
	// From here the list exists, and every later raise leaves it holding
	// what it had accumulated. Python's `except` overwrites it with [],
	// which is why the caller's reset is load-bearing and not decoration:
	// returning the partial set is what makes that observable.
	out := append([]string{}, roots...)
	allow, err := d.ConfigGet("validate.write_fence_allow", pyval.List{})
	if err != nil {
		return out, err
	}
	// `for _r in (_cfg_get(...) or [])` — a config value that is falsy
	// (None, "", 0, an empty list) iterates nothing; a value that is
	// truthy and NOT iterable raises, and that raise lands on the same
	// warning as a missing import.
	items, err := iterAny(allow)
	if err != nil {
		return out, err
	}
	for _, r := range items {
		// `if _r:` — a falsy entry is skipped BEFORE str() runs, so a
		// None in the list is not the string "None".
		if !pyval.Truthy(r) {
			continue
		}
		out = append(out, d.ExpandUser(pyval.Str(r)))
	}
	if liveRepo != "" {
		lr := d.Realpath(liveRepo)
		kept := []string{}
		for _, r := range out {
			if d.Realpath(r) != lr {
				kept = append(kept, r)
			}
		}
		out = kept
	}
	return out, nil
}

// fenceRefusal is the outer handler. It never re-raises: everything it
// does is best-effort, and the LoopResult it returns is the run.
func fenceRefusal(ctx *FenceCtx, d FenceDeps, b fenceBindings,
	cause error) *looptypes.LoopResult {
	msg := fmt.Sprintf("execution fence setup failed: %s: %s",
		excName(cause), cause)
	d.errorf("loop refused before decomposition — %s", msg)

	// Nothing agentic has run yet, so any clone or worktree created
	// during admission or fence setup is safe to remove WITHOUT a
	// merge-back.
	if ctx.ContainerClone != nil {
		if err := func() error {
			if d.Worktree == nil {
				return moduleNotFound("worktree")
			}
			if d.Worktree.CleanupClone == nil {
				return wtAttr("cleanup_clone")
			}
			if err := d.Worktree.CleanupClone(ctx.ContainerClone); err != nil {
				return err
			}
			ctx.ContainerClone = nil
			return nil
		}(); err != nil {
			d.warnf("execution fence scratch-clone cleanup failed: %s", err)
		}
	}
	if ctx.RunWorktree != nil {
		if err := func() error {
			if d.Worktree == nil {
				return moduleNotFound("worktree")
			}
			if d.Worktree.Cleanup == nil {
				return wtAttr("cleanup")
			}
			if err := d.Worktree.Cleanup(ctx.RunWorktree); err != nil {
				return err
			}
			if d.Worktree.Prune == nil {
				return wtAttr("prune")
			}
			if err := d.Worktree.Prune(ctx.RunWorktree.RepoDir); err != nil {
				return err
			}
			ctx.RunWorktree = nil
			return nil
		}(); err != nil {
			d.warnf("execution fence worktree cleanup failed: %s", err)
		}
	}

	// Best-effort neutralization for later non-loop calls in the same
	// process; the refusal itself does not depend on these succeeding.
	//
	// Each is its own try, so one failure does not skip the rest — and
	// when the fence failed AT the `from llm import ...`, the names these
	// close over were never bound, so all four raise NameError and all
	// four are reported. That double fault is the reason the loop is a
	// loop and not four statements.
	for _, n := range []struct {
		label string
		fn    func() error
	}{
		// EQUIVALENT MUTANT (kept, marked `equivalent`): testing the dep
		// for nil instead of the binding flag. b.cwd is set exactly when
		// the dep is non-nil and the deps do not change during a run, so
		// the two agree on every input. The FLAG is what the port means:
		// Python reads a NAME here, and the name is what the differential
		// drops.
		{"subprocess cwd", func() error {
			if !b.cwd {
				return unboundLocal("set_default_subprocess_cwd")
			}
			return d.SetDefaultSubprocessCwd(nil)
		}},
		{"rw-root policy", func() error {
			if !b.rw {
				return unboundLocal("set_default_container_rw_roots")
			}
			return d.SetDefaultContainerRWRoots([]string{})
		}},
		{"container suppression", func() error {
			if !b.ce {
				return unboundLocal("_ce")
			}
			if d.ContainerExec.SetContainerSuppressed == nil {
				return ceAttr("set_container_suppressed")
			}
			return d.ContainerExec.SetContainerSuppressed(true)
		}},
		{"introspection access", func() error {
			if !b.ce {
				return unboundLocal("_ce")
			}
			if d.ContainerExec.SetIntrospectionRun == nil {
				return ceAttr("set_introspection_run")
			}
			return d.ContainerExec.SetIntrospectionRun(false)
		}},
	} {
		if err := n.fn(); err != nil {
			d.warnf("execution fence refusal could not neutralize %s: %s",
				n.label, err)
		}
	}

	if ctx.ProjectSlot != nil {
		if err := ctx.ProjectSlot.Release(); err != nil {
			d.warnf("execution fence refusal project-slot release "+
				"failed: %s", err)
		} else {
			ctx.ProjectSlot = nil
		}
	}
	if ctx.RunLease != nil {
		if err := ctx.RunLease.Release(); err != nil {
			d.warnf("execution fence refusal run-lease release failed: %s",
				err)
		} else {
			ctx.RunLease = nil
		}
	}
	if err := func() error {
		if d.ClearLoopRunning == nil {
			return d.importFailure("interrupt")
		}
		return d.ClearLoopRunning()
	}(); err != nil {
		d.warnf("execution fence refusal running-state clear failed: %s",
			err)
	}
	// The last two swallow SILENTLY — `except Exception: pass`. An
	// operator who cannot see the event has already been told by the
	// error line above.
	if d.WriteEvent != nil {
		_ = d.WriteEvent("loop_done", pyval.Obj{
			{Key: "goal", Val: ctx.Goal},
			{Key: "project", Val: ctx.Project},
			{Key: "loop_id", Val: ctx.LoopID},
			{Key: "status", Val: "stuck"},
			{Key: "detail", Val: msg},
		})
	}
	// Schema owner (2026-08-15 bypass burn-down): stamp_run_stop_verdict
	// owns pair-completeness and the 800-char evidence clip, so the fence
	// hands it the raw message.
	if d.StampRunStopVerdict != nil {
		_ = d.StampRunStopVerdict(FenceRefusalStopVerdict, msg)
	}

	reason := msg
	elapsed := 0
	if d.ElapsedMS != nil {
		elapsed = d.ElapsedMS()
	}
	return &looptypes.LoopResult{
		LoopID:       ctx.LoopID,
		Goal:         ctx.Goal,
		Project:      ctx.Project,
		Steps:        nil,
		Status:       "stuck",
		StuckReason:  &reason,
		StopVerdict:  FenceRefusalStopVerdict,
		StopEvidence: msg,
		ElapsedMS:    elapsed,
	}
}

// scratchDir is in-fence scratch space: an inspectability convenience,
// NOT part of the cwd/policy safety boundary. Best-effort and visible.
func scratchDir(d FenceDeps) {
	if err := func() error {
		if d.WorkspaceRoot == nil {
			return d.importFailure("config")
		}
		root, err := d.WorkspaceRoot()
		if err != nil {
			return err
		}
		return d.MkdirParents(joinPath(root, "tmp"))
	}(); err != nil {
		d.warnf("workspace scratch directory setup skipped: %s", err)
	}
}

// joinPath is pathlib's `/`, which does NOT collapse an absolute right
// operand into the left one the way filepath.Join would — but every use
// here joins a bare name, so the simple spelling is the faithful one.
func joinPath(a, b string) string {
	if strings.HasSuffix(a, "/") {
		return a + b
	}
	return a + "/" + b
}

// iterAny is `for _r in x`, which raises TypeError on a non-iterable.
func iterAny(v any) ([]any, error) {
	switch t := v.(type) {
	case nil:
		return nil, nil
	case pyval.List:
		return []any(t), nil
	case []any:
		return t, nil
	case []string:
		out := make([]any, 0, len(t))
		for _, s := range t {
			out = append(out, s)
		}
		return out, nil
	case string:
		out := make([]any, 0, len(t))
		for _, r := range t {
			out = append(out, string(r))
		}
		return out, nil
	}
	return nil, &pyval.PyErr{Class: "TypeError",
		Msg: fmt.Sprintf("'%s' object is not iterable", pyval.TypeName(v))}
}
