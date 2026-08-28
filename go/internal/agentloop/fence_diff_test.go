package agentloop

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The execution fence is a safety decision expressed entirely as CALL
// ORDER plus one log level per outcome, so the differential records the
// calls in order with their arguments, the log lines with their levels,
// the LoopResult a refusal returns, and the four ctx fields the refusal
// is supposed to clear.
//
// The interesting inputs are failures, and they are not interchangeable:
// an ImportError at the top of the try leaves the neutralisation lambdas
// closed over names that were never bound, an AttributeError one line
// down does not, and a raise inside the clone block is a SUPPRESSION
// rather than a refusal. The spec therefore carries a per-callable
// [class, message] map — the class reaches the operator, because the
// refusal message interpolates `type(exc).__name__`.

type wtSpec struct {
	Path    string `json:"path"`
	RepoDir string `json:"repo_dir"`
}

type cloneSpec struct {
	Path   string `json:"path"`
	Branch string `json:"branch"`
}

type fenceSpec struct {
	Name string `json:"name"`

	Goal    string `json:"goal"`
	LoopID  string `json:"loop_id"`
	Project string `json:"project"`
	// CtxProject is ctx.project. Project is the `project` KEYWORD of
	// run_agent_loop. They are the same value in production and different
	// fields here on purpose — and the answer is that the keyword decides
	// nothing: `project = ctx.project` is rebound twenty lines above the
	// fence, so the fence dir, the LoopResult and the event all follow the
	// context. The port had a FenceArgs.Project field until this pair.
	CtxProject          string `json:"ctx_project"`
	IntrospectionAccess bool   `json:"introspection_access"`

	RunWorktree *wtSpec    `json:"run_worktree"`
	PresetClone *cloneSpec `json:"preset_clone"`

	HasProjectSlot bool `json:"has_project_slot"`
	HasRunLease    bool `json:"has_run_lease"`

	ProjectDirRoot string `json:"project_dir_root"`
	GoalSlug       string `json:"goal_slug"`
	WorkspaceRoot  string `json:"workspace_root"`

	ContainerConfigured bool       `json:"container_configured"`
	IsGitRepo           bool       `json:"is_git_repo"`
	Clone               *cloneSpec `json:"clone"`

	DeclaredRoots   []string `json:"declared_roots"`
	WriteFenceAllow any      `json:"write_fence_allow"`

	// Raises is keyed by callable; the value is [class, message].
	Raises map[string][]string `json:"raises"`
	// DropNames removes ONE name from its module. DeadModules removes the
	// whole module. The two are different failures in different places —
	// see FenceDeps and L59.
	DropNames   []string `json:"drop_names"`
	DeadModules []string `json:"dead_modules"`
}

func fenceScenarios() []fenceSpec {
	var out []fenceSpec
	add := func(s fenceSpec) {
		if s.Goal == "" {
			s.Goal = "ship the thing"
		}
		if s.LoopID == "" {
			s.LoopID = "L1"
		}
		if s.ProjectDirRoot == "" {
			s.ProjectDirRoot = "/pdr"
		}
		if s.GoalSlug == "" {
			s.GoalSlug = "ship-the-thing"
		}
		if s.WorkspaceRoot == "" {
			s.WorkspaceRoot = "/ws"
		}
		if s.DeclaredRoots == nil {
			s.DeclaredRoots = []string{}
		}
		if s.WriteFenceAllow == nil {
			s.WriteFenceAllow = []any{}
		}
		if s.Raises == nil {
			s.Raises = map[string][]string{}
		}
		if s.DropNames == nil {
			s.DropNames = []string{}
		}
		if s.DeadModules == nil {
			s.DeadModules = []string{}
		}
		out = append(out, s)
	}
	raise := func(k, cls, msg string) map[string][]string {
		return map[string][]string{k: {cls, msg}}
	}
	// The same, but firing only on the Nth call of that callable — the
	// resets spend the first call of two of these setters before the
	// fence body reaches its own use of them.
	raiseOn := func(k, cls, msg, n string) map[string][]string {
		return map[string][]string{k: {cls, msg, n}}
	}

	// --- the fence comes up ---------------------------------------------
	add(fenceSpec{Name: "quiet-fence-no-container"})
	add(fenceSpec{Name: "project-decides-the-fence-dir", Project: "proj",
		CtxProject: "proj"})
	add(fenceSpec{Name: "no-project-falls-back-to-the-goal-slug"})
	add(fenceSpec{Name: "a-run-worktree-is-the-fence-dir",
		RunWorktree: &wtSpec{Path: "/wt/run", RepoDir: "/wt"}})
	add(fenceSpec{Name: "introspection-access-rides-through",
		IntrospectionAccess: true})

	// --- the container clone ---------------------------------------------
	add(fenceSpec{Name: "configured-and-a-repo-gets-a-clone",
		ContainerConfigured: true, IsGitRepo: true,
		Clone: &cloneSpec{Path: "/scratch/c1", Branch: "b1"}})
	add(fenceSpec{Name: "configured-but-not-a-repo-is-untouched",
		ContainerConfigured: true, IsGitRepo: false})
	add(fenceSpec{Name: "not-configured-never-asks-about-git",
		ContainerConfigured: false, IsGitRepo: true})
	add(fenceSpec{Name: "a-preset-clone-is-not-reprovisioned",
		ContainerConfigured: true, IsGitRepo: true,
		PresetClone: &cloneSpec{Path: "/scratch/old", Branch: "old"}})
	add(fenceSpec{Name: "no-clone-suppresses-rather-than-proceeding",
		ContainerConfigured: true, IsGitRepo: true, Clone: nil})
	add(fenceSpec{Name: "a-provision-raise-is-a-suppression-not-a-refusal",
		ContainerConfigured: true, IsGitRepo: true,
		Raises: raise("provision_clone", "OSError", "disk full")})
	add(fenceSpec{Name: "a-missing-provision-clone-is-also-a-suppression",
		ContainerConfigured: true, IsGitRepo: true,
		DropNames: []string{"provision_clone"}})
	add(fenceSpec{Name: "an-is-git-repo-raise-skips-setup-and-suppresses",
		ContainerConfigured: true,
		Raises:              raise("is_git_repo", "OSError", "not a dir")})
	// container_configured() RAISING leaves _container_intended false, so
	// the handler does NOT suppress — one line apart from the case above
	// and the opposite posture.
	add(fenceSpec{Name: "container-configured-raising-does-not-suppress",
		Raises: raise("container_configured", "ValueError", "bad config")})
	add(fenceSpec{Name: "a-missing-container-configured-does-not-suppress",
		DropNames: []string{"container_configured"}})
	add(fenceSpec{Name: "a-missing-worktree-module-suppresses",
		ContainerConfigured: true, IsGitRepo: true,
		DeadModules: []string{"worktree"}})
	// The fail-closed suppression is INSIDE the inner try, so a setter
	// that raises is warned about, re-entered, and raises again — this
	// time out of the block entirely.
	add(fenceSpec{Name: "a-suppression-that-raises-refuses-the-loop",
		ContainerConfigured: true, IsGitRepo: true, Clone: nil,
		Raises: raise("set_container_suppressed", "RuntimeError", "nope")})

	// --- the rw-root set --------------------------------------------------
	add(fenceSpec{Name: "declared-roots-and-config-roots-are-joined",
		DeclaredRoots:   []string{"/a", "/b"},
		WriteFenceAllow: []any{"~/c", "/d"}})
	add(fenceSpec{Name: "a-falsy-config-entry-is-skipped-before-str",
		WriteFenceAllow: []any{"", nil, "/d", 0}})
	add(fenceSpec{Name: "a-falsy-config-value-iterates-nothing",
		WriteFenceAllow: nil})
	add(fenceSpec{Name: "a-non-iterable-config-value-costs-the-whole-set",
		DeclaredRoots: []string{"/a"}, WriteFenceAllow: 7})
	add(fenceSpec{Name: "a-string-config-value-iterates-its-characters",
		WriteFenceAllow: "ab"})
	add(fenceSpec{Name: "the-live-repo-is-dropped-from-the-rw-roots",
		ContainerConfigured: true, IsGitRepo: true,
		Clone:           &cloneSpec{Path: "/scratch/c1", Branch: "b1"},
		DeclaredRoots:   []string{"/pdr/ship-the-thing", "/keep"},
		WriteFenceAllow: []any{"/pdr/ship-the-thing"}})
	add(fenceSpec{Name: "no-live-repo-drops-nothing",
		DeclaredRoots: []string{"/pdr/ship-the-thing"}})
	add(fenceSpec{Name: "root-discovery-failure-leaves-only-the-cwd",
		DeclaredRoots: []string{"/a"},
		Raises:        raise("goal_declared_roots", "RuntimeError", "boom")})
	add(fenceSpec{Name: "a-missing-goal-declared-roots-is-the-same-line",
		DropNames: []string{"goal_declared_roots"}})
	add(fenceSpec{Name: "a-missing-config-get-is-the-same-line",
		DropNames: []string{"config_get"}})
	// A missing MODULE and a missing NAME land in the same handler and
	// say different things: ModuleNotFoundError carries CPython's own
	// "No module named 'x'", ImportError carries the name's failure.
	add(fenceSpec{Name: "a-dead-artifact-check-names-the-module",
		DeadModules: []string{"artifact_check"}})
	// The final setter is NOT protected: binding the safe fallback is
	// mandatory, so its failure refuses the loop.
	add(fenceSpec{Name: "the-rw-root-setter-failing-refuses-the-loop",
		Raises: raise("set_default_container_rw_roots", "OSError", "nope")})

	// --- refusals ----------------------------------------------------------
	// There is no dead-`llm`-MODULE fixture, and that is a finding rather
	// than a gap: `from llm import MODEL_CHEAP, MODEL_MID, MODEL_POWER`
	// runs twenty lines ABOVE the fence, outside every handler, so a
	// missing llm module raises out of run_agent_loop and never reaches
	// this try. The fence's llm guard is reachable only by a missing
	// NAME — which is exactly what the next two fixtures are, and which
	// is also enough to leave the refusal's lambdas unbound.
	add(fenceSpec{Name: "one-dropped-llm-name-fails-the-whole-statement",
		DropNames: []string{"set_default_subprocess_cwd"}})
	add(fenceSpec{Name: "the-other-dropped-llm-name-fails-it-too",
		DropNames: []string{"set_default_container_rw_roots"}})
	add(fenceSpec{Name: "a-dead-container-exec-leaves-llm-bound",
		DeadModules: []string{"container_exec"}})
	add(fenceSpec{Name: "a-missing-suppression-setter-is-an-attribute-error",
		DropNames: []string{"set_container_suppressed"}})
	add(fenceSpec{Name: "a-missing-introspection-setter-refuses-after-two-resets",
		DropNames: []string{"set_introspection_run"}})
	add(fenceSpec{Name: "a-mkdir-that-raises-refuses-the-loop",
		Raises: raise("mkdir", "PermissionError", "denied")})
	add(fenceSpec{Name: "a-cwd-setter-that-raises-refuses-the-loop",
		Raises: raise("set_default_subprocess_cwd", "OSError", "no such dir")})
	add(fenceSpec{Name: "the-first-reset-raising-refuses-before-anything",
		Raises: raise("set_container_suppressed", "RuntimeError", "boom")})

	// --- the refusal's own unwinding ---------------------------------------
	add(fenceSpec{Name: "refusal-cleans-a-clone-it-had-provisioned",
		ContainerConfigured: true, IsGitRepo: true,
		Clone:  &cloneSpec{Path: "/scratch/c1", Branch: "b1"},
		Raises: raise("set_default_subprocess_cwd", "OSError", "nope")})
	add(fenceSpec{Name: "refusal-cleans-a-preset-clone",
		PresetClone: &cloneSpec{Path: "/scratch/old", Branch: "old"},
		Raises:      raise("mkdir", "OSError", "nope")})
	add(fenceSpec{Name: "a-clone-cleanup-that-raises-keeps-the-reference",
		PresetClone: &cloneSpec{Path: "/scratch/old", Branch: "old"},
		Raises: map[string][]string{
			"mkdir":         {"OSError", "nope"},
			"cleanup_clone": {"OSError", "busy"}}})
	add(fenceSpec{Name: "refusal-cleans-and-prunes-the-run-worktree",
		RunWorktree: &wtSpec{Path: "/wt/run", RepoDir: "/wt"},
		Raises:      raise("mkdir", "OSError", "nope")})
	add(fenceSpec{Name: "a-prune-that-raises-keeps-the-worktree-reference",
		RunWorktree: &wtSpec{Path: "/wt/run", RepoDir: "/wt"},
		Raises: map[string][]string{
			"mkdir": {"OSError", "nope"},
			"prune": {"OSError", "busy"}}})
	add(fenceSpec{Name: "a-dead-worktree-module-costs-both-cleanups",
		RunWorktree: &wtSpec{Path: "/wt/run", RepoDir: "/wt"},
		PresetClone: &cloneSpec{Path: "/scratch/old", Branch: "old"},
		DeadModules: []string{"worktree"},
		Raises:      raise("mkdir", "OSError", "nope")})
	add(fenceSpec{Name: "refusal-releases-the-slot-and-the-lease",
		HasProjectSlot: true, HasRunLease: true,
		Raises: raise("mkdir", "OSError", "nope")})
	add(fenceSpec{Name: "a-slot-release-that-raises-does-not-clear-it",
		HasProjectSlot: true, HasRunLease: true,
		Raises: map[string][]string{
			"mkdir":        {"OSError", "nope"},
			"release_slot": {"OSError", "busy"}}})
	add(fenceSpec{Name: "a-lease-release-that-raises-does-not-clear-it",
		HasProjectSlot: true, HasRunLease: true,
		Raises: map[string][]string{
			"mkdir":         {"OSError", "nope"},
			"release_lease": {"OSError", "busy"}}})
	add(fenceSpec{Name: "a-neutralisation-that-raises-does-not-skip-the-rest",
		Raises: map[string][]string{
			"mkdir":                          {"OSError", "nope"},
			"set_default_subprocess_cwd":     {"OSError", "a"},
			"set_default_container_rw_roots": {"OSError", "b"},
			"set_container_suppressed":       {"OSError", "c"},
			"set_introspection_run":          {"OSError", "d"}}})
	add(fenceSpec{Name: "a-missing-clear-loop-running-is-a-warning",
		DropNames: []string{"clear_loop_running"},
		Raises:    raise("mkdir", "OSError", "nope")})
	add(fenceSpec{Name: "a-dead-interrupt-module-names-the-module",
		DeadModules: []string{"interrupt"},
		Raises:      raise("mkdir", "OSError", "nope")})
	add(fenceSpec{Name: "a-clear-loop-running-raise-is-a-warning",
		Raises: map[string][]string{
			"mkdir":              {"OSError", "nope"},
			"clear_loop_running": {"OSError", "busy"}}})
	// The last two swallow SILENTLY.
	add(fenceSpec{Name: "a-write-event-raise-is-silent",
		Raises: map[string][]string{
			"mkdir":       {"OSError", "nope"},
			"write_event": {"OSError", "busy"}}})
	add(fenceSpec{Name: "a-stamp-raise-is-silent",
		Raises: map[string][]string{
			"mkdir":                  {"OSError", "nope"},
			"stamp_run_stop_verdict": {"OSError", "busy"}}})
	add(fenceSpec{Name: "a-dead-observe-module-is-silent",
		DeadModules: []string{"observe"},
		Raises:      raise("mkdir", "OSError", "nope")})
	add(fenceSpec{Name: "a-dead-runs-module-is-silent",
		DeadModules: []string{"runs"},
		Raises:      raise("mkdir", "OSError", "nope")})
	add(fenceSpec{Name: "the-refusal-carries-the-exception-class",
		Raises: raise("mkdir", "FileNotFoundError", "no such file")})
	add(fenceSpec{Name: "the-refusal-carries-the-ctx-project",
		CtxProject: "proj", Project: "other",
		Raises: raise("mkdir", "OSError", "nope")})

	// --- the scratch dir, which is NOT part of the fence --------------------
	add(fenceSpec{Name: "a-missing-workspace-root-is-a-warning",
		DropNames: []string{"workspace_root"}})
	add(fenceSpec{Name: "a-workspace-root-raise-is-a-warning",
		Raises: raise("workspace_root", "OSError", "nope")})
	add(fenceSpec{Name: "a-scratch-mkdir-raise-is-a-warning",
		Raises: raise("mkdir_ws", "OSError", "nope")})
	add(fenceSpec{Name: "a-dead-config-module-costs-roots-and-scratch",
		DeadModules: []string{"config"}})

	// --- the rw-root set, past the point where a list exists ---------------
	// Both imports gone: the message names the FIRST statement, not the
	// one whose absence would also have been fatal.
	add(fenceSpec{Name: "both-root-imports-dead-names-the-first",
		DeadModules: []string{"artifact_check", "config"}})
	// A raise AFTER the declared roots are in hand. Python's handler
	// overwrites the partial list with [], so the bound policy is empty
	// and not the two roots it had already collected.
	add(fenceSpec{Name: "a-config-raise-discards-the-roots-already-found",
		DeclaredRoots: []string{"/a", "/b"},
		Raises:        raise("config_get", "RuntimeError", "boom")})
	// realpath() on BOTH sides of the live-repo comparison: "/pdr/./proj"
	// and "/pdr/proj/sub/.." normalise to the same path the worktree is,
	// and neither string matches it raw.
	add(fenceSpec{Name: "the-live-repo-filter-compares-resolved-paths",
		RunWorktree:         &wtSpec{Path: "/pdr/./proj", RepoDir: "/pdr"},
		ContainerConfigured: true, IsGitRepo: true,
		DeclaredRoots: []string{"/pdr/proj", "/pdr/proj/sub/..",
			"/pdr/other"}})
	// No live repo, so the filter must not run at all: "." resolves to the
	// cwd, which is what realpath("") is, and a filter that ran would drop
	// it.
	add(fenceSpec{Name: "a-cwd-shaped-root-survives-without-a-live-repo",
		DeclaredRoots: []string{"."}})

	// --- the second call of a setter the resets already used ---------------
	// Fail-closed suppression is the LAST thing the clone handler does,
	// and it is the second call of that setter. A raise there is a
	// refusal, not a swallow.
	add(fenceSpec{Name: "a-second-suppression-that-raises-refuses-the-loop",
		ContainerConfigured: true,
		Raises: map[string][]string{
			"is_git_repo":              {"OSError", "nope"},
			"set_container_suppressed": {"RuntimeError", "boom", "2"}}})
	// Same shape one setter over: binding the rw-root policy is mandatory,
	// and its first call was the reset.
	add(fenceSpec{Name: "a-second-rw-root-bind-that-raises-refuses-the-loop",
		Raises: raiseOn("set_default_container_rw_roots", "RuntimeError",
			"boom", "2")})

	return out
}

// fenceErr turns the fixture's [class, message] into the error the port
// would see. The CLASS is carried, not discarded: the refusal message
// interpolates it and an operator reads it out of stuck_reason.
func fenceErr(spec []string) error {
	if len(spec) == 0 {
		return nil
	}
	return &pyval.PyErr{Class: spec[0], Msg: spec[1]}
}

func goFenceRecord(s fenceSpec) map[string]any {
	calls := []map[string]any{}
	logs := []map[string]any{}
	rec := func(kv map[string]any) { calls = append(calls, kv) }
	at := func(level string) func(string) {
		return func(m string) {
			logs = append(logs, map[string]any{"level": level, "msg": m})
		}
	}
	counts := map[string]int{}
	raise := func(key string) error {
		spec := s.Raises[key]
		if len(spec) == 0 {
			return nil
		}
		counts[key]++
		if len(spec) > 2 && spec[2] != strconv.Itoa(counts[key]) {
			return nil
		}
		return fenceErr(spec)
	}
	dropped := map[string]bool{}
	for _, n := range s.DropNames {
		dropped[n] = true
	}
	dead := map[string]bool{}
	for _, n := range s.DeadModules {
		dead[n] = true
	}
	// The from-import modules need the absence as DATA, because their
	// nil dep alone cannot say whether the module or only the name went
	// missing — and the two raise different messages into the same
	// warning.

	ctx := &FenceCtx{LoopID: s.LoopID, Goal: s.Goal, Project: s.CtxProject}
	if s.RunWorktree != nil {
		ctx.RunWorktree = &Worktree{Path: s.RunWorktree.Path,
			RepoDir: s.RunWorktree.RepoDir}
	}
	if s.PresetClone != nil {
		ctx.ContainerClone = &Clone{Path: s.PresetClone.Path,
			Branch: s.PresetClone.Branch}
	}
	if s.HasProjectSlot {
		ctx.ProjectSlot = &recRel{"slot", rec, raise}
	}
	if s.HasRunLease {
		ctx.RunLease = &recRel{"lease", rec, raise}
	}

	d := FenceDeps{
		Info: at("info"), Warn: at("warning"), Error: at("error"),
		Debug: at("debug"), AbsentModules: dead,
		ProjectDirRoot: func() string {
			rec(map[string]any{"call": "project_dir_root"})
			return s.ProjectDirRoot
		},
		GoalToSlug: func(g string) string {
			rec(map[string]any{"call": "goal_to_slug", "goal": g})
			return s.GoalSlug
		},
		MkdirParents: func(p string) error {
			rec(map[string]any{"call": "mkdir", "path": p,
				"parents": true, "exist_ok": true})
			// Two mkdirs in two handlers: the fence dir and the workspace
			// scratch dir. The probe keys them apart by which Path built
			// them; here the only distinguishing fact is the path.
			if p == joinPath(s.WorkspaceRoot, "tmp") {
				return raise("mkdir_ws")
			}
			return raise("mkdir")
		},
		Realpath: func(p string) string {
			r, _ := pypath.Realpath(p)
			return r
		},
		ExpandUser: func(p string) string {
			r, err := pypath.ExpandUser(p)
			if err != nil {
				return p
			}
			return r
		},
		ElapsedMS: func() int { return 250 },
	}
	if !dead["llm"] {
		// Per NAME, not per statement: CPython binds a multi-name
		// from-import one name at a time, so dropping the second still
		// binds the first.
		if !dropped["set_default_subprocess_cwd"] {
			d.SetDefaultSubprocessCwd = func(dir *string) error {
				var v any
				if dir != nil {
					v = *dir
				}
				rec(map[string]any{"call": "set_default_subprocess_cwd",
					"dir": v})
				return raise("set_default_subprocess_cwd")
			}
		}
		if !dropped["set_default_container_rw_roots"] {
			d.SetDefaultContainerRWRoots = func(roots []string) error {
				if roots == nil {
					roots = []string{}
				}
				rec(map[string]any{"call": "set_default_container_rw_roots",
					"roots": roots})
				return raise("set_default_container_rw_roots")
			}
		}
	}
	if !dead["container_exec"] {
		ce := &ContainerExecMod{}
		if !dropped["set_container_suppressed"] {
			ce.SetContainerSuppressed = func(v bool) error {
				rec(map[string]any{"call": "set_container_suppressed",
					"v": v})
				return raise("set_container_suppressed")
			}
		}
		if !dropped["set_introspection_run"] {
			ce.SetIntrospectionRun = func(v bool) error {
				rec(map[string]any{"call": "set_introspection_run", "v": v})
				return raise("set_introspection_run")
			}
		}
		if !dropped["container_configured"] {
			ce.ContainerConfigured = func() (bool, error) {
				rec(map[string]any{"call": "container_configured"})
				if err := raise("container_configured"); err != nil {
					return false, err
				}
				return s.ContainerConfigured, nil
			}
		}
		d.ContainerExec = ce
	}
	if !dead["worktree"] {
		wt := &WorktreeMod{}
		if !dropped["is_git_repo"] {
			wt.IsGitRepo = func(dir string) (bool, error) {
				rec(map[string]any{"call": "is_git_repo", "dir": dir})
				if err := raise("is_git_repo"); err != nil {
					return false, err
				}
				return s.IsGitRepo, nil
			}
		}
		if !dropped["provision_clone"] {
			wt.ProvisionClone = func(dir, kind, loopID string) (*Clone,
				error) {
				rec(map[string]any{"call": "provision_clone", "dir": dir,
					"kind": kind, "loop_id": loopID})
				if err := raise("provision_clone"); err != nil {
					return nil, err
				}
				if s.Clone == nil {
					return nil, nil
				}
				return &Clone{Path: s.Clone.Path,
					Branch: s.Clone.Branch}, nil
			}
		}
		if !dropped["cleanup_clone"] {
			wt.CleanupClone = func(c *Clone) error {
				rec(map[string]any{"call": "cleanup_clone",
					"path": c.Path})
				return raise("cleanup_clone")
			}
		}
		if !dropped["cleanup"] {
			wt.Cleanup = func(w *Worktree) error {
				rec(map[string]any{"call": "cleanup", "path": w.Path})
				return raise("cleanup")
			}
		}
		if !dropped["prune"] {
			wt.Prune = func(repoDir string) error {
				rec(map[string]any{"call": "prune", "repo_dir": repoDir})
				return raise("prune")
			}
		}
		d.Worktree = wt
	}
	if !dead["artifact_check"] && !dropped["goal_declared_roots"] {
		d.GoalDeclaredRoots = func(goal string) ([]string, error) {
			rec(map[string]any{"call": "goal_declared_roots",
				"goal": goal})
			if err := raise("goal_declared_roots"); err != nil {
				return nil, err
			}
			return s.DeclaredRoots, nil
		}
	}
	if !dead["config"] {
		if !dropped["config_get"] {
			d.ConfigGet = func(key string, def any) (any, error) {
				rec(map[string]any{"call": "config_get", "key": key,
					"default": def})
				if err := raise("config_get"); err != nil {
					return nil, err
				}
				return s.WriteFenceAllow, nil
			}
		}
		if !dropped["workspace_root"] {
			d.WorkspaceRoot = func() (string, error) {
				rec(map[string]any{"call": "workspace_root"})
				if err := raise("workspace_root"); err != nil {
					return "", err
				}
				return s.WorkspaceRoot, nil
			}
		}
	}
	if !dead["interrupt"] && !dropped["clear_loop_running"] {
		d.ClearLoopRunning = func() error {
			rec(map[string]any{"call": "clear_loop_running"})
			return raise("clear_loop_running")
		}
	}
	if !dead["observe"] && !dropped["write_event"] {
		d.WriteEvent = func(event string, kv pyval.Obj) error {
			m := map[string]any{"call": "write_event", "event": event}
			for _, f := range kv {
				m[f.Key] = f.Val
			}
			rec(m)
			return raise("write_event")
		}
	}
	if !dead["runs"] && !dropped["stamp_run_stop_verdict"] {
		d.StampRunStopVerdict = func(verdict, evidence string) error {
			rec(map[string]any{"call": "stamp_run_stop_verdict",
				"stop_verdict": verdict, "stop_evidence": evidence})
			return raise("stamp_run_stop_verdict")
		}
	}

	res := SetUpExecutionFence(ctx,
		FenceArgs{IntrospectionAccess: s.IntrospectionAccess}, d)

	// The phase list belongs to run_agent_loop, not to the fence: the
	// caller sets DECOMPOSE immediately after a fence that came up, and
	// the probe's sentinel fires there. Recording it here keeps the two
	// records the same shape rather than making the Python side hide a
	// statement it really does run.
	phases := []string{}
	var result any
	if res == nil {
		phases = append(phases, "decompose")
	} else {
		result = fenceResultRecord(res)
	}
	var clonePath, wtPath any
	if ctx.ContainerClone != nil {
		clonePath = ctx.ContainerClone.Path
	}
	if ctx.RunWorktree != nil {
		wtPath = ctx.RunWorktree.Path
	}
	return map[string]any{"name": s.Name, "calls": calls, "logs": logs,
		"reached_decompose": res == nil, "result": result,
		"phases": phases, "ctx_clone": clonePath, "ctx_worktree": wtPath,
		"ctx_slot":  ctx.ProjectSlot != nil,
		"ctx_lease": ctx.RunLease != nil}
}

func fenceResultRecord(r *looptypes.LoopResult) map[string]any {
	reason := ""
	if r.StuckReason != nil {
		reason = *r.StuckReason
	}
	return map[string]any{"loop_id": r.LoopID, "goal": r.Goal,
		"project": r.Project, "status": r.Status, "steps": len(r.Steps),
		"stuck_reason": reason, "stop_verdict": r.StopVerdict,
		"stop_evidence": r.StopEvidence, "elapsed_ms": r.ElapsedMS}
}

type recRel struct {
	label string
	rec   func(map[string]any)
	raise func(string) error
}

func (r *recRel) Release() error {
	r.rec(map[string]any{"call": "release", "who": r.label})
	return r.raise("release_" + r.label)
}

func runFenceProbe(t *testing.T, dir string,
	scs []fenceSpec) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	specPath := filepath.Join(dir, "fence-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	src, err := os.ReadFile("fence_probe.py.tpl")
	if err != nil {
		t.Fatal(err)
	}
	// Through pyprobe, not exec.Command: this probe WRITES, and
	// running it by hand is how it ended up outside the sandbox,
	// the live-workspace refusal and the one shared module
	// Blocker. Two hand-rolled runners is how the last eight
	// started.
	out := pyprobe.Probe{Marker: "agent_loop.py",
		Workspace: t.TempDir()}.Run(t, string(src), srcDirAL(t),
		specPath)
	var recs []map[string]any
	if err := json.Unmarshal([]byte(out), &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

func canonFence(m map[string]any) map[string]any {
	blob, _ := json.Marshal(m)
	var out map[string]any
	_ = json.Unmarshal(blob, &out)
	return out
}

func TestExecutionFenceMatchesCPython(t *testing.T) {
	// One fixture's write-fence allow list carries `~/c`, and BOTH
	// engines expand it — the port with os.UserHomeDir, CPython with
	// Path.expanduser. Pinning HOME here is what makes them the same
	// question: without it the answer was whichever home each process
	// happened to have, which agreed only because nothing sandboxed the
	// probe. pyprobe passes a HOME a test set on purpose straight
	// through, precisely for this.
	t.Setenv("HOME", t.TempDir())
	scs := fenceScenarios()
	pyRecs := runFenceProbe(t, t.TempDir(), scs)
	if len(pyRecs) != len(scs) {
		t.Fatalf("probe returned %d records for %d scenarios",
			len(pyRecs), len(scs))
	}
	for i, s := range scs {
		t.Run(s.Name, func(t *testing.T) {
			got := canonFence(goFenceRecord(s))
			want := canonFence(pyRecs[i])
			if want["name"] != s.Name {
				t.Fatalf("record %d is %v, want %s", i, want["name"], s.Name)
			}
			a, _ := json.MarshalIndent(got, "", "  ")
			b, _ := json.MarshalIndent(want, "", "  ")
			if string(a) != string(b) {
				t.Errorf("go:\n%s\npy:\n%s", a, b)
			}
		})
	}
}

func TestFenceScenarioNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, s := range fenceScenarios() {
		if seen[s.Name] {
			t.Errorf("duplicate scenario name %q", s.Name)
		}
		seen[s.Name] = true
	}
}
