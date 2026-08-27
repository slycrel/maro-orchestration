package loopfinalize

import (
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// run_post_run_maintenance is five blocks that do not fail the same way.
// Four carry a bare `except ImportError: pass` above their general
// handler and one does not; two report at DEBUG and two at WARNING; one
// import sits INSIDE an `if` and costs nothing when the branch is not
// taken. None of that is visible in the function's shape, and all of it
// is visible in the record.

// finErr is [class, message] — the probe raises that class, and the Go
// side reproduces the class where it matters (ImportError is caught
// SILENTLY by four of the five blocks, so a callee raising it is not the
// same as a callee raising anything else).
type finErr []string

type maintSpec struct {
	Name    string `json:"name"`
	Adapter string `json:"adapter"`
	Verbose bool   `json:"verbose"`

	SkillRaise  finErr `json:"skill_raise"`
	HealthRaise finErr `json:"health_raise"`

	Scans      []any  `json:"scans"`
	ScansRaise finErr `json:"scans_raise"`
	SaveRaise  finErr `json:"save_raise"`

	Cfg      map[string]any `json:"cfg"`
	CfgRaise finErr         `json:"cfg_raise"`

	EvolverTick      bool   `json:"evolver_tick"`
	EvolverTickRaise finErr `json:"evolver_tick_raise"`
	EvolverRaise     finErr `json:"evolver_raise"`
	EvoReviewed      int    `json:"evo_reviewed"`
	EvoSuggestions   []any  `json:"evo_suggestions"`
	EvoNoAttrs       bool   `json:"evo_no_attrs"`

	InspMode      string `json:"insp_mode"`
	InspTickRaise finErr `json:"insp_tick_raise"`
	InspRaise     finErr `json:"insp_raise"`
	InspSessions  int    `json:"insp_sessions"`
	InspNoAttrs   bool   `json:"insp_no_attrs"`
	DeepPassLimit int    `json:"deep_pass_limit"`

	DropNames []string `json:"drop_names"`

	MissingEvolver      bool `json:"missing_evolver"`
	MissingSystemHealth bool `json:"missing_system_health"`
	MissingEvolverStore bool `json:"missing_evolver_store"`
	MissingConfig       bool `json:"missing_config"`
	MissingInspector    bool `json:"missing_inspector"`
}

// asErr turns the fixture's [class, message] into the Go error the port
// would see. ImportError becomes the port's own sentinel, because that is
// exactly the state a nil dep stands for and the handlers must not tell
// the two apart.
func asErr(e finErr) error {
	if len(e) == 0 {
		return nil
	}
	if e[0] == "ImportError" {
		return &pyval.PyErr{Class: "ImportError", Msg: e[1]}
	}
	return errors.New(e[1])
}

func maintScenarios() []maintSpec {
	var out []maintSpec
	add := func(s maintSpec) {
		if s.Adapter == "" {
			s.Adapter = "truthy"
		}
		if s.InspMode == "" {
			s.InspMode = "none"
		}
		if s.Scans == nil {
			s.Scans = []any{}
		}
		if s.Cfg == nil {
			s.Cfg = map[string]any{}
		}
		if s.DropNames == nil {
			s.DropNames = []string{}
		}
		if s.DeepPassLimit == 0 {
			s.DeepPassLimit = 200
		}
		out = append(out, s)
	}
	boom := func(msg string) finErr { return finErr{"Boom", msg} }
	imp := func(msg string) finErr { return finErr{"ImportError", msg} }

	// --- the happy path and the two adapters -----------------------------
	add(maintSpec{Name: "all-five-blocks-quiet"})
	add(maintSpec{Name: "verbose-rides-through", Verbose: true,
		Scans: []any{"a"},
		Cfg: map[string]any{"evolver.run_cadence": 3,
			"inspector.run_cadence": 2},
		EvolverTick: true, InspMode: "standard"})
	add(maintSpec{Name: "adapter-none", Adapter: "none"})
	// A falsy adapter is passed RAW to every callee here — nothing in this
	// function does the `adapter if adapter else None` coercion that
	// crystallisation does.
	add(maintSpec{Name: "adapter-falsy-is-passed-raw", Adapter: "falsy"})

	// --- block 1: skill maintenance --------------------------------------
	add(maintSpec{Name: "skill-maintenance-raises",
		SkillRaise: boom("promotion store is down")})
	// `except ImportError: pass` is ABOVE the general handler, so a callee
	// that raises ImportError of its own is silent too.
	add(maintSpec{Name: "skill-maintenance-raises-import-error",
		SkillRaise: imp("no module named yaml")})
	add(maintSpec{Name: "drop-run_skill_maintenance",
		DropNames: []string{"run_skill_maintenance"}})

	// --- block 2: the one with no ImportError arm -------------------------
	add(maintSpec{Name: "health-probes-raise",
		HealthRaise: boom("probe wedged")})
	// THE asymmetry: a missing system_health is REPORTED where a missing
	// evolver is silent.
	add(maintSpec{Name: "missing-system-health-is-logged",
		MissingSystemHealth: true})
	add(maintSpec{Name: "drop-run_health_probes",
		DropNames: []string{"run_health_probes"}})
	// And its ImportError arm does not exist, so a callee raising
	// ImportError IS reported here.
	add(maintSpec{Name: "health-probes-raise-import-error",
		HealthRaise: imp("no module named psutil")})

	// --- block 3: statistical scans ---------------------------------------
	add(maintSpec{Name: "scans-find-nothing-and-say-nothing"})
	add(maintSpec{Name: "scans-save-three", Scans: []any{"a", "b", "c"}})
	add(maintSpec{Name: "scans-raise", ScansRaise: boom("scanner blew up")})
	add(maintSpec{Name: "scans-save-raises", Scans: []any{"a"},
		SaveRaise: boom("suggestion store is full")})
	add(maintSpec{Name: "drop-run_statistical_scans",
		DropNames: []string{"run_statistical_scans"}})
	// _save_suggestions comes from a DIFFERENT module, imported on its own
	// statement above the call — so losing it costs the scan too.
	add(maintSpec{Name: "drop-_save_suggestions",
		DropNames: []string{"_save_suggestions"}})
	add(maintSpec{Name: "missing-evolver-store-costs-two-blocks",
		MissingEvolverStore: true,
		Cfg:                 map[string]any{"evolver.run_cadence": 3}})

	// --- block 4: the evolver cadence -------------------------------------
	add(maintSpec{Name: "evolver-cadence-off-by-default"})
	add(maintSpec{Name: "evolver-cadence-set-but-not-fired",
		Cfg: map[string]any{"evolver.run_cadence": 3}})
	add(maintSpec{Name: "evolver-cadence-fires",
		Cfg:         map[string]any{"evolver.run_cadence": 3},
		EvolverTick: true, EvoReviewed: 12,
		EvoSuggestions: []any{"s1", "s2"}})
	// getattr defaults: a report with neither attribute reads as 0 and 0,
	// which is what the Go zero value gives.
	add(maintSpec{Name: "evolver-report-has-no-attributes",
		Cfg:         map[string]any{"evolver.run_cadence": 1},
		EvolverTick: true, EvoNoAttrs: true})
	// `len(getattr(r, "suggestions", []) or [])` — None counts zero.
	add(maintSpec{Name: "evolver-suggestions-is-none",
		Cfg:         map[string]any{"evolver.run_cadence": 1},
		EvolverTick: true, EvoReviewed: 4, EvoSuggestions: nil})
	// `int(x or 0)`: every falsy config value becomes 0 without reaching
	// int() at all.
	for _, v := range []struct {
		tag string
		val any
	}{{"none", nil}, {"empty-string", ""}, {"zero", 0},
		{"false", false}, {"float", 4.9}, {"numeric-string", "7"},
		{"true", true}} {
		add(maintSpec{Name: "evolver-cadence-config-is-" + v.tag,
			Cfg: map[string]any{"evolver.run_cadence": v.val}})
	}
	// A non-numeric string DOES reach int(), and its ValueError is the
	// block's warning.
	add(maintSpec{Name: "evolver-cadence-config-is-not-a-number",
		Cfg: map[string]any{"evolver.run_cadence": "sometimes"}})
	add(maintSpec{Name: "evolver-tick-raises",
		Cfg:              map[string]any{"evolver.run_cadence": 3},
		EvolverTickRaise: boom("cadence file is corrupt")})
	add(maintSpec{Name: "evolver-run-raises",
		Cfg:          map[string]any{"evolver.run_cadence": 3},
		EvolverTick:  true,
		EvolverRaise: boom("evolver blew up")})
	// run_evolver is imported INSIDE the if, so dropping it costs nothing
	// when the cadence does not fire...
	add(maintSpec{Name: "drop-run_evolver-without-firing",
		DropNames: []string{"run_evolver"},
		Cfg:       map[string]any{"evolver.run_cadence": 3}})
	// ...and is silent (ImportError) when it does.
	add(maintSpec{Name: "drop-run_evolver-when-it-fires",
		DropNames:   []string{"run_evolver"},
		Cfg:         map[string]any{"evolver.run_cadence": 3},
		EvolverTick: true})
	add(maintSpec{Name: "drop-evolver_cadence_tick",
		DropNames: []string{"evolver_cadence_tick"}})
	add(maintSpec{Name: "config-get-raises", CfgRaise: boom("config is down")})
	add(maintSpec{Name: "missing-config-costs-two-blocks",
		MissingConfig: true})

	// --- block 5: the inspector cadence -----------------------------------
	add(maintSpec{Name: "inspector-cadence-off-by-default"})
	// The short-circuit: cadence <= 0 means the TICK IS NOT CALLED, which
	// is the whole point of the 2026-08-08 fix — a disabled inspector must
	// not create its own state file.
	add(maintSpec{Name: "inspector-cadence-zero-skips-the-tick",
		Cfg: map[string]any{"inspector.run_cadence": 0}})
	add(maintSpec{Name: "inspector-cadence-negative-skips-the-tick",
		Cfg: map[string]any{"inspector.run_cadence": -1}})
	add(maintSpec{Name: "inspector-standard-pass",
		Cfg:      map[string]any{"inspector.run_cadence": 2},
		InspMode: "standard", InspSessions: 31})
	add(maintSpec{Name: "inspector-deep-pass",
		Cfg:      map[string]any{"inspector.run_cadence": 2},
		InspMode: "deep", InspSessions: 400, DeepPassLimit: 250})
	// A mode that is neither "deep" nor "none" takes the 50 branch, and
	// the port must not turn that into a two-value enum.
	add(maintSpec{Name: "inspector-unknown-mode-takes-the-fifty",
		Cfg:      map[string]any{"inspector.run_cadence": 2},
		InspMode: "shallow"})
	add(maintSpec{Name: "inspector-tick-returns-none-mode",
		Cfg:      map[string]any{"inspector.run_cadence": 2},
		InspMode: "none"})
	// deep_every's default is 5, but `or 0` means a configured falsy value
	// is passed as 0 rather than falling back.
	add(maintSpec{Name: "inspector-deep-every-default-is-five",
		Cfg:      map[string]any{"inspector.run_cadence": 2},
		InspMode: "standard"})
	add(maintSpec{Name: "inspector-deep-every-configured-zero",
		Cfg: map[string]any{"inspector.run_cadence": 2,
			"inspector.deep_every": 0},
		InspMode: "standard"})
	add(maintSpec{Name: "inspector-deep-every-configured-none",
		Cfg: map[string]any{"inspector.run_cadence": 2,
			"inspector.deep_every": nil},
		InspMode: "standard"})
	add(maintSpec{Name: "inspector-deep-every-configured-three",
		Cfg: map[string]any{"inspector.run_cadence": 2,
			"inspector.deep_every": 3},
		InspMode: "deep"})
	add(maintSpec{Name: "inspector-deep-every-is-not-a-number",
		Cfg: map[string]any{"inspector.run_cadence": 2,
			"inspector.deep_every": "often"}})
	add(maintSpec{Name: "inspector-tick-raises",
		Cfg:           map[string]any{"inspector.run_cadence": 2},
		InspTickRaise: boom("inspector state is corrupt")})
	add(maintSpec{Name: "inspector-run-raises",
		Cfg:       map[string]any{"inspector.run_cadence": 2},
		InspMode:  "standard",
		InspRaise: boom("inspector blew up")})
	add(maintSpec{Name: "inspector-report-has-no-attributes",
		Cfg:      map[string]any{"inspector.run_cadence": 2},
		InspMode: "standard", InspNoAttrs: true})
	// DEEP_PASS_LIMIT rides the same import statement as the two
	// functions, so dropping the CONSTANT takes the whole block down.
	add(maintSpec{Name: "drop-DEEP_PASS_LIMIT",
		DropNames: []string{"DEEP_PASS_LIMIT"},
		Cfg:       map[string]any{"inspector.run_cadence": 2},
		InspMode:  "deep"})
	add(maintSpec{Name: "drop-inspector_cadence_tick",
		DropNames: []string{"inspector_cadence_tick"},
		Cfg:       map[string]any{"inspector.run_cadence": 2}})
	add(maintSpec{Name: "drop-run_inspector",
		DropNames: []string{"run_inspector"},
		Cfg:       map[string]any{"inspector.run_cadence": 2},
		InspMode:  "standard"})
	add(maintSpec{Name: "missing-inspector-module",
		MissingInspector: true,
		Cfg:              map[string]any{"inspector.run_cadence": 2}})

	// --- everything missing at once ----------------------------------------
	add(maintSpec{Name: "every-module-missing",
		MissingEvolver: true, MissingSystemHealth: true,
		MissingEvolverStore: true, MissingConfig: true,
		MissingInspector: true})

	return out
}

func goMaintenanceRecord(s maintSpec) map[string]any {
	calls := []map[string]any{}
	logs := []map[string]any{}
	rec := func(kv map[string]any) { calls = append(calls, kv) }
	at := func(level string) func(string) {
		return func(m string) {
			logs = append(logs, map[string]any{"level": level, "msg": m})
		}
	}

	var adapter any
	switch s.Adapter {
	case "truthy":
		adapter = "an adapter"
	case "falsy":
		adapter = pyval.List{}
	}

	deep := s.DeepPassLimit
	d := MaintenanceDeps{
		Info: at("info"), Warn: at("warning"), Debug: at("debug"),
		DeepPassLimit: &deep,
		RunSkillMaintenance: func(a any) error {
			rec(map[string]any{"call": "run_skill_maintenance",
				"adapter": finTag(a)})
			return asErr(s.SkillRaise)
		},
		RunHealthProbes: func() error {
			rec(map[string]any{"call": "run_health_probes"})
			return asErr(s.HealthRaise)
		},
		RunStatisticalScans: func(v bool) ([]any, error) {
			rec(map[string]any{"call": "run_statistical_scans",
				"verbose": v})
			if err := asErr(s.ScansRaise); err != nil {
				return nil, err
			}
			return s.Scans, nil
		},
		SaveSuggestions: func(sg []any) error {
			rec(map[string]any{"call": "_save_suggestions", "n": len(sg)})
			return asErr(s.SaveRaise)
		},
		ConfigGet: func(key string, def any) (any, error) {
			rec(map[string]any{"call": "config_get", "key": key,
				"default": def})
			if err := asErr(s.CfgRaise); err != nil {
				return nil, err
			}
			if v, ok := s.Cfg[key]; ok {
				return v, nil
			}
			return def, nil
		},
		EvolverCadenceTick: func(c int) (bool, error) {
			rec(map[string]any{"call": "evolver_cadence_tick",
				"cadence": c})
			if err := asErr(s.EvolverTickRaise); err != nil {
				return false, err
			}
			return s.EvolverTick, nil
		},
		RunEvolver: func(a any, v bool) (EvolverReport, error) {
			rec(map[string]any{"call": "run_evolver", "adapter": finTag(a),
				"verbose": v})
			if err := asErr(s.EvolverRaise); err != nil {
				return EvolverReport{}, err
			}
			if s.EvoNoAttrs {
				return EvolverReport{}, nil
			}
			return EvolverReport{OutcomesReviewed: s.EvoReviewed,
				Suggestions: s.EvoSuggestions}, nil
		},
		InspectorCadenceTick: func(c, de int) (string, error) {
			rec(map[string]any{"call": "inspector_cadence_tick",
				"cadence": c, "deep_every": de})
			if err := asErr(s.InspTickRaise); err != nil {
				return "", err
			}
			return s.InspMode, nil
		},
		RunInspector: func(limit int, a any, v bool) (InspectorReport,
			error) {
			rec(map[string]any{"call": "run_inspector", "limit": limit,
				"adapter": finTag(a), "verbose": v})
			if err := asErr(s.InspRaise); err != nil {
				return InspectorReport{}, err
			}
			if s.InspNoAttrs {
				return InspectorReport{}, nil
			}
			return InspectorReport{InspectedSessions: s.InspSessions}, nil
		},
	}

	dropped := map[string]bool{}
	for _, n := range s.DropNames {
		dropped[n] = true
	}
	gone := func(module bool, name string) bool {
		return module || dropped[name]
	}
	for _, x := range []struct {
		module bool
		name   string
		clear  func()
	}{
		{s.MissingEvolver, "run_skill_maintenance", func() { d.RunSkillMaintenance = nil }},
		{s.MissingEvolver, "run_statistical_scans", func() { d.RunStatisticalScans = nil }},
		{s.MissingEvolver, "run_evolver", func() { d.RunEvolver = nil }},
		{s.MissingSystemHealth, "run_health_probes", func() { d.RunHealthProbes = nil }},
		{s.MissingEvolverStore, "_save_suggestions", func() { d.SaveSuggestions = nil }},
		{s.MissingEvolverStore, "evolver_cadence_tick", func() { d.EvolverCadenceTick = nil }},
		{s.MissingConfig, "get", func() { d.ConfigGet = nil }},
		{s.MissingInspector, "inspector_cadence_tick", func() { d.InspectorCadenceTick = nil }},
		{s.MissingInspector, "run_inspector", func() { d.RunInspector = nil }},
		{s.MissingInspector, "DEEP_PASS_LIMIT", func() { d.DeepPassLimit = nil }},
	} {
		if gone(x.module, x.name) {
			x.clear()
		}
	}

	RunPostRunMaintenance(adapter, s.Verbose, d)
	return map[string]any{"name": s.Name, "calls": calls, "logs": logs}
}

func runMaintenanceProbe(t *testing.T, dir string,
	scs []maintSpec) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	specPath := filepath.Join(dir, "maint-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "maintenance_probe.py.tpl", srcDirLF(t),
		specPath)
	out, err := cmd.Output()
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			t.Fatalf("probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("probe failed: %v", err)
	}
	var recs []map[string]any
	if err := json.Unmarshal(out, &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

func TestPostRunMaintenanceMatchesCPython(t *testing.T) {
	scs := maintScenarios()
	pyRecs := runMaintenanceProbe(t, t.TempDir(), scs)
	if len(pyRecs) != len(scs) {
		t.Fatalf("probe returned %d records for %d scenarios",
			len(pyRecs), len(scs))
	}
	for i, s := range scs {
		t.Run(s.Name, func(t *testing.T) {
			got := canonMint(goMaintenanceRecord(s))
			want := canonMint(pyRecs[i])
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

func TestMaintenanceScenarioNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, s := range maintScenarios() {
		if seen[s.Name] {
			t.Errorf("duplicate scenario name %q", s.Name)
		}
		seen[s.Name] = true
	}
}
