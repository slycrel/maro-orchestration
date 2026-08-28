package agentloop

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

//go:generate true

// headSpec is one scenario, sent to both engines.
//
// Kwargs carries only the OVERRIDES. Everything else comes from each
// engine's own defaults — Python's from the signature, Go's from
// defaultHeadArgs — so a default the port got wrong shows up as a
// disagreement in the recorded _initialize_loop call rather than as a
// fixture nobody wrote.
type headSpec struct {
	Name          string         `json:"name"`
	Goal          string         `json:"goal"`
	Kwargs        map[string]any `json:"kwargs"`
	EarlyReturn   bool           `json:"early_return"`
	EarlyStatus   string         `json:"early_status"`
	InitRaises    bool           `json:"init_raises"`
	ExitRaises    bool           `json:"exit_raises"`
	ScopeRaises   bool           `json:"scope_raises"`
	DropTierConst string         `json:"drop_tier_const"`
	DeadModules   []string       `json:"dead_modules"`
	ModelCheap    string         `json:"model_cheap"`
	ModelMid      string         `json:"model_mid"`
	ModelPower    string         `json:"model_power"`
	CtxLoopID     string         `json:"ctx_loop_id"`
	CtxStartTS    float64        `json:"ctx_start_ts"`
	CtxProject    string         `json:"ctx_project"`
}

// headMarker is the probe's Marker: an opaque value passed through
// untouched, distinguishable in a record from one that was rebuilt.
type headMarker struct{ tag string }

func (m headMarker) String() string { return "<Marker " + m.tag + ">" }

// headRender is pv(): the canonical rendering both engines produce.
func headRender(v any) any {
	switch t := v.(type) {
	case nil:
		return nil
	case headMarker:
		return t.String()
	case int:
		return float64(t)
	case float64, string, bool:
		return t
	}
	return fmt.Sprintf("<%T>", v)
}

type headRecord struct {
	Name         string           `json:"name"`
	ReachedFence bool             `json:"reached_fence"`
	Result       map[string]any   `json:"result"`
	Cls          string           `json:"cls"`
	Msg          string           `json:"msg"`
	Calls        []map[string]any `json:"calls"`
	CtxChannel   any              `json:"ctx_channel"`
}

// defaultHeadArgs is run_agent_loop's signature, spelled out. Every value
// here is a claim about the Python default, and the differential is what
// checks the claim.
func defaultHeadArgs(goal string) HeadArgs {
	return HeadArgs{
		Goal:                 goal,
		Project:              nil,
		RepoPath:             "",
		Model:                nil,
		Backend:              nil,
		Adapter:              nil,
		KnowledgeSubGoals:    false,
		MaxSteps:             8,
		MaxIterations:        40,
		DryRun:               false,
		Verbose:              false,
		InterruptQueue:       nil,
		HookRegistry:         nil,
		AncestryContextExtra: "",
		StepCallback:         nil,
		ParallelFanOut:       0,
		TokenBudget:          nil,
		CostBudget:           nil,
		RalphVerify:          false,
		ResumeFromLoopID:     nil,
		PermissionContext:    nil,
		ContinuationDepth:    0,
		PresetSteps:          nil,
		Channel:              headMarker{"channel"},
		LoopReason:           "initial",
		ParentLoopID:         nil,
		AdmissionWaitS:       nil,
		DeferLearning:        false,
		DeferMaintenance:     false,
		MeasurementClass:     "",
		HandleID:             "",
		IntrospectionAccess:  false,
		RecoveryInProgress:   false,
	}
}

// applyHeadKwargs is the Go side of the probe's override map.
func applyHeadKwargs(t *testing.T, a *HeadArgs, kw map[string]any) {
	t.Helper()
	marker := func(v any) any {
		if s, ok := v.(string); ok && len(s) > 7 && s[:7] == "marker:" {
			return headMarker{s[7:]}
		}
		return v
	}
	num := func(v any) int {
		f, ok := v.(int)
		if !ok {
			t.Fatalf("kwarg %v is not an int", v)
		}
		return f
	}
	for k, v := range kw {
		switch k {
		case "project":
			a.Project = marker(v)
		case "repo_path":
			a.RepoPath = v.(string)
		case "model":
			a.Model = marker(v)
		case "backend":
			a.Backend = marker(v)
		case "adapter":
			a.Adapter = marker(v)
		case "knowledge_sub_goals":
			a.KnowledgeSubGoals = v.(bool)
		case "max_steps":
			a.MaxSteps = num(v)
		case "max_iterations":
			a.MaxIterations = num(v)
		case "dry_run":
			a.DryRun = v.(bool)
		case "verbose":
			a.Verbose = v.(bool)
		case "interrupt_queue":
			a.InterruptQueue = marker(v)
		case "hook_registry":
			a.HookRegistry = marker(v)
		case "ancestry_context_extra":
			a.AncestryContextExtra = v.(string)
		case "step_callback":
			a.StepCallback = marker(v)
		case "parallel_fan_out":
			a.ParallelFanOut = num(v)
		case "token_budget":
			a.TokenBudget = v
		case "cost_budget":
			a.CostBudget = v
		case "ralph_verify":
			a.RalphVerify = v.(bool)
		case "resume_from_loop_id":
			a.ResumeFromLoopID = v
		case "permission_context":
			a.PermissionContext = marker(v)
		case "continuation_depth":
			a.ContinuationDepth = num(v)
		case "preset_steps":
			a.PresetSteps = v
		case "loop_reason":
			a.LoopReason = v.(string)
		case "parent_loop_id":
			a.ParentLoopID = v
		case "admission_wait_s":
			a.AdmissionWaitS = v
		case "defer_learning":
			a.DeferLearning = v.(bool)
		case "defer_maintenance":
			a.DeferMaintenance = v.(bool)
		case "measurement_class":
			a.MeasurementClass = v.(string)
		case "handle_id":
			a.HandleID = v.(string)
		case "introspection_access":
			a.IntrospectionAccess = v.(bool)
		default:
			t.Fatalf("scenario sets %q, which applyHeadKwargs does not "+
				"know — the two engines would receive different arguments", k)
		}
	}
}

// errReachedFence mirrors the probe's BaseException sentinel.
var errReachedFence = errors.New("reached fence")

// goHeadRecord runs the port over one scenario and produces the probe's
// record.
func goHeadRecord(t *testing.T, sc headSpec) headRecord {
	t.Helper()
	var calls []map[string]any
	rec := func(name string, kw ...[2]any) {
		pairs := make([]any, 0, len(kw))
		for _, p := range kw {
			pairs = append(pairs, []any{p[0], headRender(p[1])})
		}
		calls = append(calls, map[string]any{"call": name, "kw": pairs})
	}

	a := defaultHeadArgs(sc.Goal)
	applyHeadKwargs(t, &a, sc.Kwargs)

	ctx := &HeadCtx{
		LoopID:         sc.CtxLoopID,
		StartTS:        sc.CtxStartTS,
		Project:        sc.CtxProject,
		Adapter:        headMarker{"ctx-adapter"},
		InterruptQueue: headMarker{"ctx-queue"},
		PermCtx:        headMarker{"ctx-perm"},
	}
	channelSet := false
	dead := map[string]bool{}
	for _, m := range sc.DeadModules {
		dead[m] = true
	}

	d := HeadDeps{
		InitializeLoop: func(goal string, kw pyval.Obj) (*HeadCtx,
			*looptypes.LoopResult, error) {
			pairs := []any{[]any{"goal", headRender(goal)}}
			for _, f := range kw {
				pairs = append(pairs, []any{f.Key, headRender(f.Val)})
			}
			calls = append(calls, map[string]any{
				"call": "_initialize_loop", "kw": pairs})
			if sc.InitRaises {
				return nil, nil, &pyval.PyErr{Class: "ValueError",
					Msg: "initialize refused"}
			}
			if sc.EarlyReturn {
				return ctx, &looptypes.LoopResult{LoopID: "early",
					Goal: sc.Goal, Status: sc.EarlyStatus}, nil
			}
			return ctx, nil, nil
		},
		ImportLoopIDScope: func() error {
			if dead["captains_log"] {
				return &pyval.PyErr{Class: "ModuleNotFoundError",
					Msg: "No module named 'captains_log'"}
			}
			return nil
		},
		LoopIDScope: func(loopID string) (func() error, error) {
			rec("loop_id_scope.enter", [2]any{"loop_id", loopID})
			if sc.ScopeRaises {
				return nil, &pyval.PyErr{Class: "RuntimeError",
					Msg: "scope refused"}
			}
			return func() error {
				rec("loop_id_scope.exit", [2]any{"loop_id", loopID})
				if sc.ExitRaises {
					return &pyval.PyErr{Class: "RuntimeError",
						Msg: "scope exit failed"}
				}
				return nil
			}, nil
		},
		ImportTierConstants: func() (string, string, string, error) {
			if sc.DropTierConst != "" {
				return "", "", "", &pyval.PyErr{Class: "ImportError",
					Msg: fmt.Sprintf("cannot import name '%s' from 'llm'",
						sc.DropTierConst)}
			}
			return sc.ModelCheap, sc.ModelMid, sc.ModelPower, nil
		},
	}
	d.Body = func(b Bound) (*looptypes.LoopResult, error) {
		channelSet = true
		rec("fence.reached", [2]any{"suppressed", false})
		return nil, errReachedFence
	}

	out := headRecord{Name: sc.Name}
	res, err := RunAgentLoopHead(a, d)
	switch {
	case errors.Is(err, errReachedFence):
		out.ReachedFence = true
	case err != nil:
		out.Cls = pyval.ClassOf(err)
		out.Msg = err.Error()
	case res != nil:
		out.Result = map[string]any{"loop_id": res.LoopID,
			"status": res.Status}
	}
	out.Calls = calls
	if channelSet || ctx.Channel != nil {
		out.CtxChannel = headRender(ctx.Channel)
	} else {
		out.CtxChannel = "<unset>"
	}
	return out
}

func headScenarios() []headSpec {
	base := func(name string) headSpec {
		return headSpec{Name: name, Goal: "ship the thing",
			Kwargs: map[string]any{}, ModelCheap: "cheap",
			ModelMid: "mid", ModelPower: "power",
			CtxLoopID: "L-7", CtxStartTS: 100.0, CtxProject: "from-ctx",
			EarlyStatus: "done"}
	}
	with := func(name string, f func(*headSpec)) headSpec {
		s := base(name)
		f(&s)
		return s
	}
	return []headSpec{
		// The twenty-six keywords, at their signature defaults. This is
		// the scenario that checks defaultHeadArgs against the real
		// signature: a default the port guessed wrong shows up here.
		base("the initialize call at every default"),

		// The same call with every override set to something that is not
		// the default, so a port that dropped a keyword cannot pass by
		// coincidence.
		with("every keyword overridden", func(s *headSpec) {
			s.Kwargs = map[string]any{
				"project": "p-arg", "repo_path": "/repo",
				"model": "m", "backend": "b", "adapter": "marker:adapter",
				"knowledge_sub_goals": true, "max_steps": 3,
				"max_iterations": 9, "dry_run": true, "verbose": true,
				"interrupt_queue":        "marker:queue",
				"hook_registry":          "marker:hooks",
				"ancestry_context_extra": "ancestry",
				"step_callback":          "marker:cb", "parallel_fan_out": 4,
				"token_budget": 111, "cost_budget": 2.5,
				"ralph_verify": true, "resume_from_loop_id": "L-0",
				"permission_context": "marker:perm",
				"continuation_depth": 2, "preset_steps": "marker:steps",
				"loop_reason": "recovery", "parent_loop_id": "L-p",
				"admission_wait_s": 1.5, "defer_learning": true,
				"defer_maintenance": true, "measurement_class": "organic",
				"handle_id": "H-1", "introspection_access": true,
			}
		}),

		// The early return. The scope is never entered, so nothing after
		// it happens — including the channel assignment.
		with("an early return short-circuits everything",
			func(s *headSpec) { s.EarlyReturn = true }),

		// And the ordering that makes that observable: the captains_log
		// import is BELOW the early return, so a run that never starts
		// cannot be killed by a missing captains_log.
		with("a missing captains_log is invisible on the early path",
			func(s *headSpec) {
				s.EarlyReturn = true
				s.DeadModules = []string{"captains_log"}
			}),
		with("a missing captains_log is fatal on the normal path",
			func(s *headSpec) { s.DeadModules = []string{"captains_log"} }),

		// The scope's own failure modes. A `with` whose setup raises has
		// no context manager to exit.
		with("loop_id_scope refuses at call time",
			func(s *headSpec) { s.ScopeRaises = true }),

		// The tier import is INSIDE the scope, so its failure must leave
		// the scope the way any other exception does — the exit call is
		// in the record.
		with("the tier import fails inside the scope",
			func(s *headSpec) { s.DropTierConst = "MODEL_MID" }),

		// The normal path: enter, assign, import, reach the fence, and
		// exit on the way out.
		with("the fence is reached inside the scope", func(s *headSpec) {}),

		// _initialize_loop's own failure. Nothing below it runs, and in
		// particular the captains_log import does not.
		with("initialize raises", func(s *headSpec) { s.InitRaises = true }),

		// `is not None` is the ONLY test. A result whose status is empty
		// looks falsy and still ends the run — the reason the port spells
		// this as a nil check rather than as truthiness.
		with("an early return with an empty status is still returned",
			func(s *headSpec) {
				s.EarlyReturn = true
				s.EarlyStatus = ""
			}),

		// The exit's own failure, alone and against a body that is also
		// failing. Python lets __exit__'s exception REPLACE the body's,
		// which is the only way to tell "the exit ran" from "the exit's
		// failure was swallowed".
		with("the scope exit fails and the body did not",
			func(s *headSpec) {
				s.ExitRaises = true
				s.EarlyReturn = false
			}),
		with("the scope exit fails on top of a failing body",
			func(s *headSpec) { s.ExitRaises = true }),

		// Booleans that do NOT all agree. Every bool set to true makes a
		// mutation that swaps two of them invisible, and two of them are
		// adjacent in the keyword list.
		with("the booleans do not all agree", func(s *headSpec) {
			s.Kwargs = map[string]any{
				"knowledge_sub_goals": false, "dry_run": true,
				"verbose": false, "ralph_verify": true,
				"defer_learning": true, "defer_maintenance": false,
				"introspection_access": true,
			}
		}),
	}
}

func TestHeadMatchesCPython(t *testing.T) {
	scs := headScenarios()
	py := runHeadProbe(t, t.TempDir(), scs)
	if len(py) != len(scs) {
		t.Fatalf("probe returned %d records for %d scenarios", len(py), len(scs))
	}
	for i, sc := range scs {
		t.Run(sc.Name, func(t *testing.T) {
			stripImportPath(py[i])
			got := goHeadRecord(t, sc)
			a, _ := json.MarshalIndent(canonHead(got), "", " ")
			b, _ := json.MarshalIndent(py[i], "", " ")
			if string(a) != string(b) {
				t.Errorf("record differs\n go: %s\n py: %s", a, b)
			}
		})
	}
}

// stripImportPath drops the module FILE CPython appends to an
// ImportError — "cannot import name 'X' from 'llm' (/…/src/llm.py)".
//
// That suffix is the path the probe was pointed at, not a decision either
// engine makes, and it is different on every machine. It is stripped
// rather than reproduced because a port that hard-coded a source path
// would be asserting something false. Only the "cannot import name"
// message is touched, so a divergence in any other message still shows.
func stripImportPath(rec map[string]any) {
	msg, _ := rec["msg"].(string)
	const prefix = "cannot import name "
	if len(msg) < len(prefix) || msg[:len(prefix)] != prefix {
		return
	}
	if i := strings.LastIndex(msg, " ("); i > 0 &&
		msg[len(msg)-1] == ')' {
		rec["msg"] = msg[:i]
	}
}

// canonHead round-trips the Go record through the same decoder the probe's
// output goes through, so the comparison is of DECODED values on both
// sides and not of Go's types against JSON's.
func canonHead(r headRecord) map[string]any {
	blob, _ := json.Marshal(r)
	var out map[string]any
	_ = json.Unmarshal(blob, &out)
	return out
}

func runHeadProbe(t *testing.T, dir string, scs []headSpec) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	specPath := filepath.Join(dir, "head-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	src, err := os.ReadFile("head_probe.py.tpl")
	if err != nil {
		t.Fatal(err)
	}
	// The probe drives the real run_agent_loop, which imports config on
	// the way past. Workspace is named because "read-only" is not a
	// judgement this file gets to make about a function it does not own.
	out := pyprobe.Probe{Marker: "agent_loop.py", Workspace: t.TempDir()}.
		Run(t, string(src), pyprobe.SrcDir(t, "agent_loop.py"), specPath)
	var recs []map[string]any
	if err := json.Unmarshal([]byte(out), &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

func TestHeadScenarioNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, sc := range headScenarios() {
		if seen[sc.Name] {
			t.Errorf("duplicate scenario name %q — a table whose names "+
				"collide reports one subtest for two fixtures", sc.Name)
		}
		seen[sc.Name] = true
	}
}

// TestTierOrderCollapsesLikeADictLiteral is NOT a differential, and the
// reason is worth stating: `_TIER_ORDER` is a LOCAL, built after the tier
// import and before the fence, and CPython offers no observation point
// between those two lines. It reaches the phases eventually — the spine's
// differential compares the `tier_order` keyword the execute phase
// receives — but by then three distinct constants have already been
// folded into three entries and the collapse cannot be seen.
//
// So the property is pinned here, against the dict literal's rule rather
// than against a run: a repeated key keeps the LAST index, and a map with
// fewer than three entries is what two equal tier constants produce.
// Neither is hypothetical; the tiers are configuration strings.
func TestTierOrderCollapsesLikeADictLiteral(t *testing.T) {
	run := func(cheap, mid, power string) map[string]int {
		var got map[string]int
		d := HeadDeps{
			InitializeLoop: func(string, pyval.Obj) (*HeadCtx,
				*looptypes.LoopResult, error) {
				return &HeadCtx{LoopID: "L"}, nil, nil
			},
			ImportLoopIDScope: func() error { return nil },
			LoopIDScope: func(string) (func() error, error) {
				return func() error { return nil }, nil
			},
			ImportTierConstants: func() (string, string, string, error) {
				return cheap, mid, power, nil
			},
		}
		d.Body = func(b Bound) (*looptypes.LoopResult, error) {
			got = b.TierOrder
			return nil, errReachedFence
		}
		if _, err := RunAgentLoopHead(defaultHeadArgs("g"), d); err == nil {
			t.Fatal("the body did not run")
		}
		return got
	}
	if got := run("cheap", "mid", "power"); len(got) != 3 ||
		got["cheap"] != 0 || got["mid"] != 1 || got["power"] != 2 {
		t.Errorf("three distinct tiers = %v, want cheap:0 mid:1 power:2", got)
	}
	// `{X: 0, X: 1, power: 2}` is a two-entry dict whose X is 1.
	if got := run("same", "same", "power"); len(got) != 2 || got["same"] != 1 {
		t.Errorf("cheap == mid = %v, want two entries with same:1", got)
	}
	// And the last one wins even when it is the last key in the literal.
	if got := run("cheap", "same", "same"); len(got) != 2 || got["same"] != 2 {
		t.Errorf("mid == power = %v, want two entries with same:2", got)
	}
}

// TestBoundValuesComeFromTheContextNotTheArguments pins the six rebinds.
//
// Also not a differential, and for a sharper reason than the tier map: the
// rebinds have no observation point ANYWHERE in this span. `project =
// ctx.project` is a local assignment, and the first thing that reads it is
// the execution fence — a different chunk, with its own probe, whose
// `project-is-the-context-not-the-keyword` fixture is exactly this
// property observed where CPython can show it. The same is true of the
// other five: the spine's differential is where they surface.
//
// What is left here is the seam between those chunks, and it is worth a
// test of its own because the port has already got this wrong once. The
// fence read the KEYWORD, picked a different directory for the entire run,
// and every type in the program agreed with it.
func TestBoundValuesComeFromTheContextNotTheArguments(t *testing.T) {
	ctx := &HeadCtx{
		LoopID:         "from-ctx",
		StartTS:        222.0,
		Project:        "project-from-ctx",
		Adapter:        headMarker{"adapter-from-ctx"},
		InterruptQueue: headMarker{"queue-from-ctx"},
		PermCtx:        headMarker{"perm-from-ctx"},
	}
	// Every argument is a DIFFERENT value from the context's, so a port
	// that read the argument cannot pass by coincidence.
	a := defaultHeadArgs("g")
	a.Project = "project-from-arg"
	a.Adapter = headMarker{"adapter-from-arg"}
	a.InterruptQueue = headMarker{"queue-from-arg"}
	a.PermissionContext = headMarker{"perm-from-arg"}

	var got Bound
	d := HeadDeps{
		InitializeLoop: func(string, pyval.Obj) (*HeadCtx,
			*looptypes.LoopResult, error) {
			return ctx, nil, nil
		},
		ImportLoopIDScope: func() error { return nil },
		LoopIDScope: func(string) (func() error, error) {
			return func() error { return nil }, nil
		},
		ImportTierConstants: func() (string, string, string, error) {
			return "c", "m", "p", nil
		},
	}
	d.Body = func(b Bound) (*looptypes.LoopResult, error) {
		got = b
		return nil, errReachedFence
	}
	if _, err := RunAgentLoopHead(a, d); !errors.Is(err, errReachedFence) {
		t.Fatalf("the body did not run: %v", err)
	}
	if got.Ctx != ctx {
		t.Error("Bound.Ctx is not the context _initialize_loop returned")
	}
	for _, c := range []struct {
		field string
		got   any
		want  any
	}{
		{"LoopID", got.LoopID, ctx.LoopID},
		{"StartTS", got.StartTS, ctx.StartTS},
		{"Project", got.Project, ctx.Project},
		{"Adapter", got.Adapter, ctx.Adapter},
		{"InterruptQueue", got.InterruptQueue, ctx.InterruptQueue},
		{"PermCtx", got.PermCtx, ctx.PermCtx},
	} {
		if got, want := fmt.Sprint(c.got), fmt.Sprint(c.want); got != want {
			t.Errorf("Bound.%s = %s, want the context's %s — the argument "+
				"of the same name is a different value and it is NOT the "+
				"one that wins", c.field, got, want)
		}
	}
	// The assignment, which is the head's only write to the context.
	if fmt.Sprint(ctx.Channel) != fmt.Sprint(a.Channel) {
		t.Errorf("ctx.Channel = %v, want the channel keyword %v",
			ctx.Channel, a.Channel)
	}
}

// TestTheBodysResultIsWhatTheFunctionReturns is the third Go-only test
// here, and it exists because of where the probe's sentinel sits.
//
// The head's last line hands back whatever the rest of run_agent_loop
// produced. CPython's observation point for that is a COMPLETE run — the
// spine's probe drives the real function all the way to a LoopResult and
// compares it — but the head's probe stops at the fence's first call, so
// in THIS differential the body never returns at all. A mutation that
// dropped the result survived the battery for exactly that reason: not
// because no input can see it, but because no input in this table can.
func TestTheBodysResultIsWhatTheFunctionReturns(t *testing.T) {
	want := &looptypes.LoopResult{LoopID: "L-7", Goal: "g", Status: "done"}
	d := HeadDeps{
		InitializeLoop: func(string, pyval.Obj) (*HeadCtx,
			*looptypes.LoopResult, error) {
			return &HeadCtx{LoopID: "L-7"}, nil, nil
		},
		ImportLoopIDScope: func() error { return nil },
		LoopIDScope: func(string) (func() error, error) {
			return func() error { return nil }, nil
		},
		ImportTierConstants: func() (string, string, string, error) {
			return "c", "m", "p", nil
		},
		Body: func(Bound) (*looptypes.LoopResult, error) { return want, nil },
	}
	got, err := RunAgentLoopHead(defaultHeadArgs("g"), d)
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if got != want {
		t.Errorf("the function returned %+v, not the body's result %+v",
			got, want)
	}
}
