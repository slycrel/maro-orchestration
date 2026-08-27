package looptypes

import (
	"encoding/json"
	"errors"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"

	"strconv"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

func srcDirLT(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// probeLT runs one python3 snippet against the real src/ tree.
//
// `loop_types` is import-safe by construction (its whole docstring is about
// that), so it is imported at the top with no stubbing. `config` IS stubbed
// in the snippets that need it -- see the resolve probe, where the stub is
// the only way to drive the config arm without reading this box's real
// config, and where reading the real one would make the fixture depend on
// the machine.
func probeLT(t *testing.T, body string, args ...string) []byte {
	t.Helper()
	argv := append([]string{"-c",
		"import json,sys\nsys.path.insert(0, sys.argv[1])\n" + body,
		srcDirLT(t)}, args...)
	out, err := exec.Command("python3", argv...).Output()
	if err != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("the CPython probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("the CPython probe could not run: %v", err)
	}
	return out
}

// probeLTStdin is probeLT with the payload on STDIN. The oversize
// contribution fixtures are 128KB of astral characters each, which is past
// this box's argv ceiling -- the first attempt died with "argument list too
// long", which is a fixture that never ran rather than a fixture that
// passed.
func probeLTStdin(t *testing.T, body, stdin string) []byte {
	t.Helper()
	cmd := exec.Command("python3", "-c",
		"import json,sys\nsys.path.insert(0, sys.argv[1])\n"+body,
		srcDirLT(t))
	cmd.Stdin = strings.NewReader(stdin)
	out, err := cmd.Output()
	if err != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("the CPython probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("the CPython probe could not run: %v", err)
	}
	return out
}

func decodeLT(t *testing.T, raw []byte, out any) {
	t.Helper()
	if err := json.Unmarshal(raw, out); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, raw)
	}
}

// ---------------------------------------------------------------------
// The logging attribute table
// ---------------------------------------------------------------------

// _configure_logging resolves its level with `hasattr(logging, env_level)`
// and `getattr(logging, env_level)`, NOT with a lookup in the four names
// its docstring lists. That makes the CONTENTS OF THE `logging` MODULE'S
// NAMESPACE part of this port's contract, and the two tables in
// looptypes.go are a transcription of it.
//
// A transcription is exactly the thing that can be wrong on the day it is
// written and wrong again when the interpreter moves, so this censuses the
// namespace instead of spot-checking the names someone thought of. It
// found two errors in the first table on 2026-08-27:
//
//	WARN     an int level (30) that WAS listed as absent, in a comment
//	         claiming to have measured it. `MARO_LOG_LEVEL=WARN --verbose`
//	         resolved to WARNING in CPython and DEBUG here.
//	_STYLES  a dict, upper-cased already, so it passes hasattr and then
//	         raises out of setLevel. It was in neither table, so the port
//	         answered WARNING where CPython aborted the run.
//
// Only names that EQUAL their own upper-case can ever be reached, because
// the env value is `.upper()`ed before the hasattr -- so `getLogger` is
// unreachable and correctly absent from both tables.
func TestTheLoggingAttrTableIsTheOneCPythonHas(t *testing.T) {
	raw := probeLT(t, ""+
		"import logging\n"+
		"out = {}\n"+
		"for name in dir(logging):\n"+
		"    if name != name.upper():\n"+
		"        continue\n"+
		"    v = getattr(logging, name)\n"+
		"    is_int = isinstance(v, int) and not isinstance(v, bool)\n"+
		"    raised = ''\n"+
		"    try:\n"+
		"        logging.getLogger('probe.census').setLevel(v)\n"+
		"    except Exception as e:\n"+
		"        raised = type(e).__name__\n"+
		"    out[name] = [is_int, (v if is_int else None), raised]\n"+
		"print(json.dumps(out))")
	var py map[string][]any
	decodeLT(t, raw, &py)

	if len(py) < 8 {
		t.Fatalf("dir(logging) yielded only %d upper-cased names; the probe "+
			"is not reading the module it thinks it is", len(py))
	}

	var ints, others int
	for name, row := range py {
		isInt, _ := row[0].(bool)
		raised, _ := row[2].(string)
		// hasattr answers TRUE for every one of these, whichever table the
		// name is in. Asserting it here is what keeps loggingHasAttr's int
		// branch a tested branch rather than dead code defended by a
		// comment: it is unreachable from ResolveLogLevel, which consults
		// the level table first.
		if !loggingHasAttr(name) {
			t.Errorf("hasattr(logging, %q) is true and loggingHasAttr says "+
				"otherwise", name)
		}
		if isInt {
			ints++
			want := int(row[1].(float64))
			got, ok := loggingIntAttrs[name]
			if !ok {
				t.Errorf("logging.%s is an int level (%d) and loggingIntAttrs "+
					"does not list it: MARO_LOG_LEVEL=%s resolves there and "+
					"falls through to the debug/verbose arm here", name, want, name)
				continue
			}
			if got != want {
				t.Errorf("logging.%s: go %d py %d", name, got, want)
			}
			if raised != "" {
				t.Errorf("logging.%s is an int and setLevel still raised %s; the "+
					"int/non-int split is not the right one", name, raised)
			}
			continue
		}
		others++
		// A non-int attribute is only a "badAttr" if setLevel actually
		// refuses it. Asserting that rather than assuming it is what makes
		// ResolveLogLevel's second return value a measured claim: a
		// non-int that setLevel ACCEPTED (a str naming a registered level)
		// would need a third arm, and this is the test that would say so.
		if raised == "" {
			t.Errorf("logging.%s is not an int and setLevel ACCEPTED it, so "+
				"badAttr is the wrong model for it", name)
		}
		if !loggingOtherAttrs[name] {
			t.Errorf("logging.%s passes hasattr, is not an int, and raises %s "+
				"out of setLevel -- and loggingOtherAttrs does not list it, so "+
				"MARO_LOG_LEVEL=%s aborts the run there and resolves quietly here",
				name, raised, strings.ToLower(name))
		}
	}

	// The reverse direction: an entry in either table that CPython does not
	// have. This is the arm that fires on an interpreter upgrade removing a
	// deprecated alias, which is a real prospect for WARN.
	for name := range loggingIntAttrs {
		if name != pytext.Upper(name) {
			t.Errorf("loggingIntAttrs has %q, which is not its own upper-case "+
				"and so can never be reached: the env value is upper()ed "+
				"before the hasattr", name)
			continue
		}
		if _, ok := py[name]; !ok {
			t.Errorf("loggingIntAttrs lists %q and dir(logging) does not have "+
				"it. The interpreter moved; the table has to move with it", name)
		}
	}
	for name := range loggingOtherAttrs {
		if _, ok := py[name]; !ok {
			t.Errorf("loggingOtherAttrs lists %q and dir(logging) does not "+
				"have it", name)
		}
	}
	if ints < 7 || others < 1 {
		t.Fatalf("the census reached %d int names and %d others; too few for "+
			"the sweep to be doing anything", ints, others)
	}
}

// ---------------------------------------------------------------------
// ResolveLogLevel
// ---------------------------------------------------------------------

type logScenario struct {
	Level   string `json:"level"`
	Debug   string `json:"debug"`
	Cfg     any    `json:"cfg"`
	CfgFail bool   `json:"cfg_fail"`
	Verbose bool   `json:"verbose"`
	why     string
}

// Both env vars are read with a plain `os.environ.get`, so UNSET and SET-TO-
// EMPTY are the same input and only one of them needs a row.
var logScenarios = []logScenario{
	// The five documented rungs, in order.
	{Level: "DEBUG", why: "rung 1"},
	{Debug: "1", why: "rung 2"},
	{Cfg: true, why: "rung 3"},
	{Verbose: true, why: "rung 4"},
	{why: "rung 5, the quiet default"},

	// Rung 1 beats every later rung, including the raising ones.
	{Level: "ERROR", Debug: "1", Cfg: true, Verbose: true, why: "env level wins"},
	// ...and the config lookup is SKIPPED, not merely outranked: a config
	// that raises cannot affect a run that named its level. The two are
	// indistinguishable in the answer, which is why the row pairs with the
	// one below it.
	{Level: "ERROR", CfgFail: true, why: "config never consulted"},
	{CfgFail: true, Verbose: true, why: "config raised; verbose still wins"},
	{CfgFail: true, why: "config raised -> False, not True"},
	// MARO_DEBUG=1 also skips the config lookup.
	{Debug: "1", CfgFail: true, why: "debug skips the config arm"},

	// MARO_DEBUG is `== "1"` and nothing else.
	{Debug: "0", why: "not 1"},
	{Debug: "true", why: "not 1"},
	{Debug: " 1", why: "not 1 -- no strip"},
	{Debug: "1", Verbose: false, why: "exactly 1"},

	// bool() is Python truthiness, not a yaml-ish parse. The string
	// "false" is TRUE, and so is a non-empty list.
	{Cfg: "false", why: "a non-empty string is truthy"},
	{Cfg: "", why: "an empty string is not"},
	{Cfg: 0, why: "zero"},
	{Cfg: 1, why: "one"},
	{Cfg: nil, why: "None"},
	{Cfg: []any{}, why: "empty list"},
	{Cfg: []any{0}, why: "a list holding a falsy element is still truthy"},
	{Cfg: map[string]any{}, why: "empty dict"},
	{Cfg: map[string]any{"a": 1}, why: "non-empty dict"},

	// Every int level in the module namespace, reached by name.
	{Level: "CRITICAL", why: "50"},
	{Level: "FATAL", why: "50, the CRITICAL alias"},
	{Level: "ERROR", why: "40"},
	{Level: "WARNING", why: "30"},
	{Level: "INFO", why: "20"},
	{Level: "DEBUG", why: "10"},
	{Level: "NOTSET", why: "0 -- a real answer, not a miss"},

	// THE FINDING. logging.WARN exists on this box (3.14) and the port's
	// table said it did not. verbose=false hides it -- both sides answer
	// 30, one by resolving the name and one by falling through to the
	// default -- so the row that can fail is the verbose one.
	{Level: "WARN", why: "the deprecated alias, which still exists"},
	{Level: "WARN", Verbose: true, why: "THE finding: 30 there, DEBUG here"},
	{Level: "WARN", Debug: "1", why: "same divergence through rung 2"},

	// THE SECOND FINDING. Upper-cased already, present in the namespace,
	// not an int: hasattr passes and setLevel raises.
	{Level: "_STYLES", why: "a dict -> the run aborts"},
	{Level: "_styles", why: "...and it is reachable in lower case"},
	{Level: "_styles", Verbose: true, why: "still aborts; verbose is rung 4"},
	{Level: "BASIC_FORMAT", why: "a str -> Unknown level"},
	{Level: "basic_format", Debug: "1", why: "aborts ahead of rung 2"},

	// A name that is not in the namespace at all falls through to the
	// later rungs -- it does NOT abort, and it does NOT become WARNING by
	// itself.
	{Level: "TRACE", why: "no such attribute"},
	{Level: "TRACE", Verbose: true, why: "falls through to rung 4"},
	{Level: "bogus", Cfg: true, why: "falls through to rung 3"},

	// str.upper() is Unicode. U+0131 DOTLESS I upper-cases to ASCII I, so
	// this reaches logging.INFO through a character that is not an i.
	{Level: "\u0131nfo", why: "dotless i -> INFO"},
	{Level: "\u0131NFO", Verbose: true, why: "same, and rung 1 still wins"},
	// U+0130 upper-cases to itself, so this one MISSES and falls through.
	// The pair is what shows the resolution is the upper-case and not a
	// fold: a case-insensitive compare would match both.
	{Level: "\u0130nfo", Verbose: true, why: "dotted I does NOT reach INFO"},
	{Level: "info", why: "the ASCII control for the pair"},

	// ...and str.upper() EXPANDS. U+FB05/U+FB06 LATIN SMALL LIGATURE
	// LONG S T / ST both upper-case to the two characters "ST", which is
	// how a five-character env value reaches the seven-character
	// `_STYLES` and aborts the run. strings.ToUpper leaves the ligature
	// alone and resolves quietly, so this pair is what makes pytext.Upper
	// load-bearing in this ladder rather than merely faithful.
	//
	// Found by sweeping all 1.1M code points against the table
	// (TestTheUpperExpansionsThatCanReachALevelNameAreAllFixtured), NOT
	// by review: the comment this replaced asserted the opposite, and had
	// survived a mutation of the call site.
	{Level: "_\ufb05yles", why: "LONG S T ligature -> _STYLES -> abort"},
	{Level: "_\ufb06yles", Verbose: true, why: "the ST ligature, same"},
}

func TestResolveLogLevelMatchesCPython(t *testing.T) {
	in, err := json.Marshal(logScenarios)
	if err != nil {
		t.Fatal(err)
	}
	raw := probeLT(t, ""+
		"import logging, os, types\n"+
		// The config module is STUBBED, not read. _configure_logging does
		// `from config import get`, and letting that reach this box's real
		// config would make the fixture depend on the machine -- and on
		// whether some other test had written to it.
		"cfg = types.ModuleType('config')\n"+
		"state = {}\n"+
		"def _get(key, default=None):\n"+
		"    if state['fail']:\n"+
		"        raise RuntimeError('config unreadable')\n"+
		"    return state['value']\n"+
		"cfg.get = _get\n"+
		"sys.modules['config'] = cfg\n"+
		"import loop_types as lt\n"+
		"out = []\n"+
		"for sc in json.loads(sys.argv[2]):\n"+
		"    state['fail'] = sc['cfg_fail']\n"+
		"    state['value'] = sc['cfg']\n"+
		"    os.environ['MARO_LOG_LEVEL'] = sc['level']\n"+
		"    os.environ['MARO_DEBUG'] = sc['debug']\n"+
		"    lt._logging_configured = False\n"+
		"    logging.getLogger('maro').setLevel(logging.NOTSET)\n"+
		"    try:\n"+
		"        lt._configure_logging(sc['verbose'])\n"+
		"        out.append([logging.getLogger('maro').level, ''])\n"+
		"    except Exception as e:\n"+
		"        out.append([None, type(e).__name__])\n"+
		"print(json.dumps(out))",
		string(in))
	var py [][]any
	decodeLT(t, raw, &py)
	if len(py) != len(logScenarios) {
		t.Fatalf("probe returned %d rows for %d scenarios", len(py), len(logScenarios))
	}

	var raised, debugs, warnings, other int
	for i, sc := range logScenarios {
		level, badAttr := ResolveLogLevel(LogEnv{
			MaroLogLevel: sc.Level,
			MaroDebug:    sc.Debug,
			ConfigDebug:  sc.Cfg,
			ConfigFailed: sc.CfgFail,
		}, sc.Verbose)
		pyRaise, _ := py[i][1].(string)
		desc := "level=" + strconv.Quote(sc.Level) + " debug=" + strconv.Quote(sc.Debug) +
			" cfg=" + reprAny(sc.Cfg) + " cfg_fail=" + strconv.FormatBool(sc.CfgFail) +
			" verbose=" + strconv.FormatBool(sc.Verbose) + "  (" + sc.why + ")"

		if pyRaise != "" {
			raised++
			if badAttr == "" {
				t.Errorf("CPython ABORTED the run with %s and this port "+
					"resolved level %d\n  %s", pyRaise, level, desc)
			}
			continue
		}
		if badAttr != "" {
			t.Errorf("this port reports the non-level attribute %q and "+
				"CPython resolved %v\n  %s", badAttr, py[i][0], desc)
			continue
		}
		want := int(py[i][0].(float64))
		if level != want {
			t.Errorf("LOG LEVEL disagrees\n  go %d\n  py %d\n  %s", level, want, desc)
		}
		switch want {
		case LevelDebug:
			debugs++
		case LevelWarning:
			warnings++
		default:
			other++
		}
	}
	// A corpus that never reaches an abort, or only ever lands on the
	// default, cannot fail in the direction either finding lay.
	if raised < 3 || debugs < 3 || warnings < 3 || other < 3 {
		t.Fatalf("the corpus is lopsided: %d aborts, %d DEBUG, %d WARNING, "+
			"%d other levels", raised, debugs, warnings, other)
	}
}

func reprAny(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return "?"
	}
	return string(b)
}

// ---------------------------------------------------------------------
// step_from_decompose
// ---------------------------------------------------------------------

// kwargsOf derives the CPython call from the Go one, so the two sides of
// every row have a single source. Writing the kwargs out by hand beside
// each StepOpts is how a fixture ends up testing a call nobody made.
//
// A nil pointer / nil slice means the keyword is OMITTED, which is what
// makes Python reach for its own default -- and the three defaults that
// are not the zero value (status "pending", confidence "unverified",
// ended_ts "now") are the entire reason this factory exists.
func kwargsOf(o StepOpts) map[string]any {
	kw := map[string]any{
		"result":                   o.Result,
		"iteration":                o.Iteration,
		"tokens_in":                o.TokensIn,
		"tokens_out":               o.TokensOut,
		"cache_read_tokens":        o.CacheReadTokens,
		"provider_cost_usd":        o.ProviderCostUSD,
		"elapsed_ms":               o.ElapsedMS,
		"call_record":              o.CallRecord,
		"executor_session_id":      o.ExecutorSessionID,
		"executor_session_resumed": o.ExecutorSessionResumed,
		"started_ts":               o.StartedTS,
		"model":                    o.Model,
		"model_tier":               o.ModelTier,
		"tier_escalated_from":      o.TierEscalatedFrom,
		"venue":                    o.Venue,
		"artifact_check":           o.ArtifactCheck,
	}
	if o.Status != nil {
		kw["status"] = *o.Status
	}
	if o.Confidence != nil {
		kw["confidence"] = *o.Confidence
	}
	if o.InjectedSteps != nil {
		kw["injected_steps"] = o.InjectedSteps
	}
	if o.EndedTS != nil {
		kw["ended_ts"] = *o.EndedTS
	}
	return kw
}

type stepScenario struct {
	text  string
	index int
	opts  StepOpts
	why   string
}

var stepScenarios = []stepScenario{
	{"", 0, StepOpts{}, "everything defaulted"},
	{"fetch the page", 3, StepOpts{}, "the ordinary mint"},

	// The three defaults that are not zero values, each pinned and each
	// overridden. status="pending" is NOT one of StepOutcome's documented
	// statuses -- the factory mints a step that has not run yet.
	{"s", 1, StepOpts{Status: Ptr("done")}, "status overridden"},
	{"s", 1, StepOpts{Status: Ptr("")}, "status explicitly empty"},
	{"s", 1, StepOpts{Confidence: Ptr("strong")}, "confidence overridden"},
	{"s", 1, StepOpts{Confidence: Ptr("")}, "confidence explicitly empty"},

	// THE SENTINEL. Ptr("") opts OUT of the "now" default and leaves the
	// field genuinely empty, which is what makes loop_report fall back to
	// an explicitly-flagged approximate timeline. A plain "" that silently
	// became "now" would fabricate a timestamp for every checkpoint-resume
	// and parallel-batch step.
	{"s", 1, StepOpts{EndedTS: Ptr("")}, "ended_ts opted OUT"},
	{"s", 1, StepOpts{EndedTS: Ptr("2026-08-27T00:00:00+00:00")}, "ended_ts given"},

	// injected_steps: None becomes a FRESH list, and an empty list is not
	// None. The mutable-default hazard lives here in both languages.
	{"s", 1, StepOpts{InjectedSteps: []string{}}, "an empty list, not None"},
	{"s", 1, StepOpts{InjectedSteps: []string{"a", "b"}}, "two injected"},

	// Negative and large numerics: the fields are ints in Python with no
	// clamping, and a port that made them uints would fail here.
	{"s", -1, StepOpts{Iteration: -5, TokensIn: -1, ElapsedMS: -1000},
		"negatives pass straight through"},
	{"s", 1 << 40, StepOpts{TokensIn: 1 << 40, TokensOut: 1 << 40},
		"beyond 32 bits"},

	// provider_cost_usd is a float. 0.1+0.2 is the classic, and it must be
	// the same wrong number on both sides.
	{"s", 1, StepOpts{ProviderCostUSD: 0.1 + 0.2}, "float, not decimal"},

	// Text is carried, never normalised: no strip, no case fold, no
	// newline collapse. The separators are the ones the two runtimes
	// disagree about elsewhere in this port, so a stray Strip here shows.
	{"  padded  ", 1, StepOpts{}, "no strip"},
	{"a\x1fb", 1, StepOpts{}, "U+001F is whitespace to Python only"},
	{"a\u2028b", 1, StepOpts{}, "U+2028 splits str.splitlines"},
	{"café 研究", 1, StepOpts{}, "non-ASCII"},
	{"İı", 1, StepOpts{}, "the Turkish pair"},
	{"emoji \U0001f600", 1, StepOpts{}, "astral"},
	{"quote\"back\\slash", 1, StepOpts{}, "JSON-hostile"},

	// Everything at once, so a field dropped from the constructor shows up
	// even if no single-field row covers it.
	{"all", 7, StepOpts{
		Status: Ptr("blocked"), Result: "r", Iteration: 2, TokensIn: 11,
		TokensOut: 22, CacheReadTokens: 3, ProviderCostUSD: 1.5, ElapsedMS: 44,
		Confidence: Ptr("weak"), InjectedSteps: []string{"x"},
		CallRecord: "build/calls/call-00001.json", ExecutorSessionID: "sess",
		ExecutorSessionResumed: true, EndedTS: Ptr("2026-01-01T00:00:00+00:00"),
		StartedTS: "2025-12-31T23:59:00+00:00", Model: "claude-opus-5",
		ModelTier: "power", TierEscalatedFrom: "mid", Venue: "container:maro",
		ArtifactCheck: "judged",
	}, "every field non-default"},
}

func TestStepFromDecomposeMatchesCPython(t *testing.T) {
	type call struct {
		Text  string         `json:"text"`
		Index int            `json:"index"`
		KW    map[string]any `json:"kw"`
	}
	calls := make([]call, 0, len(stepScenarios))
	for _, sc := range stepScenarios {
		calls = append(calls, call{sc.text, sc.index, kwargsOf(sc.opts)})
	}
	in, err := json.Marshal(calls)
	if err != nil {
		t.Fatal(err)
	}
	// The rows that OMIT ended_ts reach the live clock on both sides, so
	// the probe pins CPython's -- `loop_types` did `from datetime import
	// datetime`, which makes lt.datetime the patchable name -- and hands
	// back the exact string it pinned for the Go clock to use. Comparing
	// two live clocks would fail every run; skipping the field on those
	// rows would leave the sentinel's DEFAULT arm uncompared, which is the
	// arm the factory exists for.
	raw := probeLT(t, ""+
		"import dataclasses\n"+
		"from datetime import datetime as _dt, timezone as _tz\n"+
		"import loop_types as lt\n"+
		"FIXED = _dt(2026, 8, 27, 12, 34, 56, 789012, tzinfo=_tz.utc)\n"+
		"class _Clock:\n"+
		"    @staticmethod\n"+
		"    def now(tz=None):\n"+
		"        return FIXED\n"+
		"lt.datetime = _Clock\n"+
		"out = []\n"+
		"for c in json.loads(sys.argv[2]):\n"+
		"    o = lt.step_from_decompose(c['text'], c['index'], **c['kw'])\n"+
		"    out.append(dataclasses.asdict(o))\n"+
		"print(json.dumps({'fixed': FIXED.isoformat(), 'rows': out}))",
		string(in))
	var probe struct {
		Fixed string           `json:"fixed"`
		Rows  []map[string]any `json:"rows"`
	}
	decodeLT(t, raw, &probe)
	py := probe.Rows
	if len(py) != len(stepScenarios) {
		t.Fatalf("probe returned %d rows for %d scenarios", len(py), len(stepScenarios))
	}
	if probe.Fixed == "" {
		t.Fatal("the probe did not pin a clock")
	}
	restore := SetNowForTest(func() string { return probe.Fixed })
	defer restore()

	// ...and the pin has to have BITTEN. If lt.datetime stopped being the
	// name the factory reads, every omitted-ended_ts row would silently
	// compare a live CPython clock against the pinned Go one and fail --
	// but a future rewrite that made both sides fall back to "" would pass
	// while testing nothing.
	var pinned int
	for i, sc := range stepScenarios {
		if sc.opts.EndedTS == nil {
			pinned++
			if py[i]["ended_ts"] != probe.Fixed {
				t.Fatalf("row %d omits ended_ts and CPython did not use the "+
					"pinned clock (%v vs %q): lt.datetime is no longer the "+
					"name step_from_decompose reads",
					i, py[i]["ended_ts"], probe.Fixed)
			}
		}
	}
	if pinned < 3 {
		t.Fatalf("only %d rows exercise the ended_ts default arm", pinned)
	}

	for i, sc := range stepScenarios {
		got := asPyDict(t, StepFromDecompose(sc.text, sc.index, sc.opts))
		want := py[i]
		if len(got) != len(want) {
			t.Fatalf("row %d (%s): the Go outcome renders %d fields and the "+
				"dataclass has %d -- a field was added on one side only\n"+
				"  go  %v\n  py  %v", i, sc.why, len(got), len(want),
				sortedKeys(got), sortedKeys(want))
		}
		for _, k := range sortedKeys(want) {
			g, w := got[k], want[k]
			if reprAny(g) != reprAny(w) {
				t.Errorf("row %d (%s) field %s\n  go %s\n  py %s",
					i, sc.why, k, reprAny(g), reprAny(w))
			}
		}
	}
}

// asPyDict renders a StepOutcome under the dataclass's own field names, so
// the comparison is against `dataclasses.asdict` and not against a shape
// invented here. Both sides go through encoding/json before the compare so
// an int and a float that happen to be equal are not reported as different.
func asPyDict(t *testing.T, o StepOutcome) map[string]any {
	t.Helper()
	// The mapping itself lives on the type (StepOutcome.AsDict) because a
	// second differential needs it too, and two copies of a 24-field
	// mapping is how they start disagreeing about which fields exist.
	b, err := json.Marshal(o.AsDict())
	if err != nil {
		t.Fatal(err)
	}
	var out map[string]any
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func sortedKeys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// The ended_ts default is a CLOCK, so it cannot be compared value-for-value
// against a second process. What CAN be compared is the SPELLING: Python
// writes `datetime.now(timezone.utc).isoformat()`, which drops the
// fractional part entirely on a whole second and writes "+00:00" rather
// than "Z". A port that emitted RFC3339 with a "Z" would pass every row
// above -- they all pass ended_ts explicitly -- and write a different
// timestamp into every real run record.
func TestTheEndedTSDefaultIsSpelledTheWayCPythonSpellsIt(t *testing.T) {
	raw := probeLT(t, ""+
		"import loop_types as lt\n"+
		"from datetime import datetime, timezone\n"+
		"o = lt.step_from_decompose('s', 0)\n"+
		"whole = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc).isoformat()\n"+
		"frac  = datetime(2026, 8, 27, 12, 0, 0, 123456, tzinfo=timezone.utc).isoformat()\n"+
		"print(json.dumps([o.ended_ts, whole, frac]))")
	var py []string
	decodeLT(t, raw, &py)

	shape := regexp.MustCompile(
		`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?\+00:00$`)
	if !shape.MatchString(py[0]) {
		t.Fatalf("CPython's own ended_ts does not match the shape this test "+
			"asserts, so the assertion is the thing that is wrong: %q", py[0])
	}
	if live := StepFromDecompose("s", 0, StepOpts{}).EndedTS; !shape.MatchString(live) {
		t.Errorf("the Go default ended_ts is spelled differently: %q", live)
	}

	// ...and the two boundary spellings, pinned exactly. `restore` runs
	// before the compare rather than at the end of the loop body, so a
	// failure cannot leave the package clock replaced for every later test
	// in the binary.
	for i, want := range []string{py[1], py[2]} {
		restore := SetNowForTest(func() string { return want })
		got := StepFromDecompose("s", 0, StepOpts{}).EndedTS
		restore()
		if got != want {
			t.Errorf("boundary %d: go %q py %q", i, got, want)
		}
	}
	if strings.Contains(py[1], ".") {
		t.Error("the whole-second spelling grew a fractional part; the " +
			"microsecond-elision claim has moved")
	}
}

// Python hands the caller's LIST to the dataclass without copying, so a
// later mutation by the caller is visible through the outcome. The port
// shares the slice header for the same reason -- and a slice header is NOT
// a list object, so a caller-side APPEND that reallocates is invisible
// where Python's would be seen. Both halves are measured here rather than
// asserted in prose, because the divergent half is the one a reader would
// not expect.
//
// Recorded, NOT reconciled: making the append half agree would mean
// copying the slice, which breaks the in-place half that production leans
// on. It is an inconsequential difference in the only sense that matters
// -- no call site appends to a list it has already handed over -- and the
// test is what keeps that "no call site does" honest by failing loudly if
// the behaviour ever flips.
func TestInjectedStepsAliasingMatchesCPythonWhereItCan(t *testing.T) {
	raw := probeLT(t, ""+
		"import loop_types as lt\n"+
		"caller = ['a']\n"+
		"o = lt.step_from_decompose('s', 0, injected_steps=caller)\n"+
		"caller[0] = 'MUTATED'\n"+
		"in_place = list(o.injected_steps)\n"+
		"caller.append('b')\n"+
		"after_append = list(o.injected_steps)\n"+
		"fresh = lt.step_from_decompose('s', 0)\n"+
		"fresh2 = lt.step_from_decompose('s', 0)\n"+
		"fresh.injected_steps.append('x')\n"+
		"print(json.dumps([in_place, after_append, fresh2.injected_steps]))")
	var py [][]string
	decodeLT(t, raw, &py)

	caller := []string{"a"}
	o := StepFromDecompose("s", 0, StepOpts{InjectedSteps: caller})
	caller[0] = "MUTATED"
	if !eqStrsLT(o.InjectedSteps, py[0]) {
		t.Errorf("an IN-PLACE write through the caller's slice: go %v py %v",
			o.InjectedSteps, py[0])
	}
	if len(py[0]) != 1 || py[0][0] != "MUTATED" {
		t.Fatalf("CPython did not alias the caller's list at all, so the "+
			"premise of this test has moved: %v", py[0])
	}

	caller = append(caller, "b")
	_ = caller
	if len(o.InjectedSteps) == len(py[1]) {
		t.Errorf("a caller-side APPEND became visible through the outcome. "+
			"That is Python's behaviour and not this port's, so either the "+
			"port started copying or this test stopped measuring: go %v py %v",
			o.InjectedSteps, py[1])
	}

	// The mutable-default hazard: two default-built outcomes must not share
	// one list.
	f1 := StepFromDecompose("s", 0, StepOpts{})
	f2 := StepFromDecompose("s", 0, StepOpts{})
	f1.InjectedSteps = append(f1.InjectedSteps, "x")
	if len(f2.InjectedSteps) != len(py[2]) {
		t.Errorf("two default-built outcomes SHARE an injected_steps list: "+
			"go %v py %v", f2.InjectedSteps, py[2])
	}
}

func eqStrsLT(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// ---------------------------------------------------------------------
// LoopResult.summary
// ---------------------------------------------------------------------

type summaryScenario struct {
	LoopID     string   `json:"loop_id"`
	Project    string   `json:"project"`
	Goal       string   `json:"goal"`
	Status     string   `json:"status"`
	Steps      []string `json:"steps"`
	Stuck      *string  `json:"stuck_reason"`
	Verdict    string   `json:"stop_verdict"`
	TokensIn   int      `json:"total_tokens_in"`
	TokensOut  int      `json:"total_tokens_out"`
	ElapsedMS  int      `json:"elapsed_ms"`
	LogPath    *string  `json:"log_path"`
	Interrupts int      `json:"interrupts_applied"`
	Nines      bool     `json:"march_of_nines_alert"`
	why        string
}

var summaryScenarios = []summaryScenario{
	{why: "every field at its zero value"},
	{LoopID: "loop-1", Project: "maro", Goal: "ship the port", Status: "done",
		Steps: []string{"done", "done"}, TokensIn: 100, TokensOut: 20,
		ElapsedMS: 4500, why: "the ordinary success"},

	// The step tally counts "done" and "blocked" and nothing else, and the
	// denominator is EVERY step -- so a run of skipped steps reads 0/3.
	{Steps: []string{"done", "blocked", "skipped"}, why: "one of each"},
	{Steps: []string{"skipped", "skipped", "skipped"}, why: "0/3, not 0/0"},
	{Steps: []string{"pending"}, why: "a step that never ran"},
	{Steps: []string{"DONE"}, why: "the compare is case-SENSITIVE"},
	{Steps: []string{"done ", " blocked"}, why: "...and it does not strip"},

	// The four conditional lines. interrupts_applied is tested for
	// TRUTHINESS, not for positivity, so a negative one still renders.
	{Interrupts: 1, why: "interrupts render"},
	{Interrupts: 0, why: "...and zero does not"},
	{Interrupts: -1, why: "a negative count is truthy"},
	{Nines: true, why: "the alert line is the literal True"},

	// stuck_reason and log_path are Optional. None and "" take the same
	// branch (both falsy) and a port that tested `!= nil` would render an
	// empty one.
	{Stuck: Ptr("no adapter"), why: "stuck_reason renders through repr"},
	{Stuck: Ptr(""), why: "an EMPTY stuck_reason renders nothing"},
	{Stuck: nil, why: "None likewise"},
	{LogPath: Ptr("/tmp/x.log"), why: "log_path renders RAW, not repr'd"},
	{LogPath: Ptr(""), why: "an empty log_path renders nothing"},

	// stop_verdict rides beside stuck_reason and can coexist with
	// status=done: the landing synthesis ends an out-of-budget run "done"
	// and the verdict is what keeps the cap-hit visible.
	{Status: "done", Verdict: "out-of-budget", why: "verdict WITH done"},
	{Status: "stuck", Verdict: "thesis-refuted", Stuck: Ptr("dead end"),
		why: "both, and the order between them is the contract"},

	// goal goes through repr(), which picks its own quote character, keeps
	// non-ASCII literal, and escapes control characters. Every one of
	// these is a line that lands in an operator-facing log.
	{Goal: "it's quoted", why: "an apostrophe flips repr to double quotes"},
	{Goal: `he said "hi"`, why: "a double quote does not"},
	{Goal: `both ' and "`, why: "...and both together escapes"},
	{Goal: "a\nb", why: "a newline inside the value"},
	{Goal: "a\tb", why: "a tab"},
	{Goal: "back\\slash", why: "a backslash"},
	{Goal: "café 研究", why: "non-ASCII stays literal in py3 repr"},
	{Goal: "\U0001f600", why: "astral"},
	{Goal: "a\x1fb", why: "a C0 control repr escapes"},
	{Goal: "a\u2028b", why: "U+2028 -- repr escapes it, str does not"},
	{Goal: "a\u0085b", why: "U+0085 NEXT LINE"},
	{Goal: "İı", why: "the Turkish pair"},

	// Multi-line free text in every string field at once: the joiner is a
	// newline, so a field carrying one is indistinguishable in the output
	// from a new line of the summary. That is Python's behaviour and the
	// port has to reproduce it, not improve on it.
	{LoopID: "a\nb", Project: "c\nd", Status: "e\nf",
		LogPath: Ptr("g\nh"), why: "newlines in the UNQUOTED fields"},

	{LoopID: "loop-9", Project: "p", Goal: "g", Status: "stuck",
		Steps: []string{"done", "blocked", "blocked"}, Stuck: Ptr("stalled"),
		Verdict: "lost-the-plot", TokensIn: 7, TokensOut: 8, ElapsedMS: 9,
		LogPath: Ptr("/l"), Interrupts: 2, Nines: true,
		why: "every conditional line at once, in order"},
}

func TestLoopResultSummaryMatchesCPython(t *testing.T) {
	in, err := json.Marshal(summaryScenarios)
	if err != nil {
		t.Fatal(err)
	}
	raw := probeLT(t, ""+
		"import loop_types as lt\n"+
		"out = []\n"+
		"for sc in json.loads(sys.argv[2]):\n"+
		"    steps = [lt.step_from_decompose('s', i, status=st, ended_ts='')\n"+
		"             for i, st in enumerate(sc['steps'] or [])]\n"+
		"    r = lt.LoopResult(\n"+
		"        loop_id=sc['loop_id'], project=sc['project'], goal=sc['goal'],\n"+
		"        status=sc['status'], steps=steps,\n"+
		"        stuck_reason=sc['stuck_reason'],\n"+
		"        stop_verdict=sc['stop_verdict'],\n"+
		"        total_tokens_in=sc['total_tokens_in'],\n"+
		"        total_tokens_out=sc['total_tokens_out'],\n"+
		"        elapsed_ms=sc['elapsed_ms'], log_path=sc['log_path'],\n"+
		"        interrupts_applied=sc['interrupts_applied'],\n"+
		"        march_of_nines_alert=sc['march_of_nines_alert'])\n"+
		"    out.append(r.summary())\n"+
		"print(json.dumps(out))",
		string(in))
	var py []string
	decodeLT(t, raw, &py)
	if len(py) != len(summaryScenarios) {
		t.Fatalf("probe returned %d rows for %d scenarios", len(py), len(summaryScenarios))
	}

	var withConditional int
	for i, sc := range summaryScenarios {
		r := NewLoopResult(sc.LoopID, sc.Project, sc.Goal, sc.Status)
		for j, st := range sc.Steps {
			r.Steps = append(r.Steps, StepFromDecompose("s", j,
				StepOpts{Status: Ptr(st), EndedTS: Ptr("")}))
		}
		r.StuckReason = sc.Stuck
		r.StopVerdict = sc.Verdict
		r.TotalTokensIn = sc.TokensIn
		r.TotalTokensOut = sc.TokensOut
		r.ElapsedMS = sc.ElapsedMS
		r.LogPath = sc.LogPath
		r.InterruptsApplied = sc.Interrupts
		r.MarchOfNinesAlert = sc.Nines

		if got := r.Summary(); got != py[i] {
			t.Errorf("SUMMARY row %d (%s) differs -- this is the line an "+
				"operator reads\n  go %q\n  py %q", i, sc.why, got, py[i])
		}
		if strings.Count(py[i], "\n") > 6 {
			withConditional++
		}
	}
	if withConditional < 5 {
		t.Fatalf("only %d rows render any conditional line; the four "+
			"`if` arms are barely exercised", withConditional)
	}
}

// ---------------------------------------------------------------------
// render_contributions
// ---------------------------------------------------------------------

var renderScenarios = [][]ContextContribution{
	// THE HARD CONTRACT: zero contributions render to "" so prompts stay
	// byte-identical to the pre-seam behavior. A port that joined an empty
	// slice would also produce "", which is why the next row matters --
	// one record must NOT be wrapped in anything.
	{},
	{{Source: "budget", Kind: "context", Text: "you have 20% of the budget left"}},
	{
		{Source: "budget", Kind: "context", Text: "one"},
		{Source: "hook", Kind: "note", Text: "two"},
	},
	// The label carries the SOURCE and not the kind. A port that rendered
	// "[budget/context]" passes a one-record test written from the struct.
	{{Source: "escalate_reply", Kind: "reply", Text: "go ahead"}},
	// Empty pieces: render_contributions does no filtering of its own --
	// that is the ledger's job -- so an empty text still gets a label and
	// an empty source still gets brackets.
	{{Source: "", Kind: "", Text: ""}},
	{{Source: "s", Kind: "k", Text: ""}, {Source: "", Kind: "k", Text: "t"}},
	// Text carrying the separator the joiner uses: the rendering is
	// ambiguous, and reproducing the ambiguity is the port's job.
	{{Source: "a", Kind: "k", Text: "x\n\ny"}, {Source: "b", Kind: "k", Text: "z"}},
	{{Source: "a\nb", Kind: "k", Text: "t"}},
	{{Source: "]", Kind: "k", Text: "["}},
	{{Source: "研究", Kind: "k", Text: "\U0001f600 café"}},
	{{Source: "a\u2028b", Kind: "k", Text: "c\x1fd"}},
}

func TestRenderContributionsMatchesCPython(t *testing.T) {
	in, err := json.Marshal(renderScenarios)
	if err != nil {
		t.Fatal(err)
	}
	raw := probeLT(t, ""+
		"import loop_types as lt\n"+
		"out = []\n"+
		"for rows in json.loads(sys.argv[2]):\n"+
		"    recs = [lt.ContextContribution(source=r['Source'], kind=r['Kind'],\n"+
		"                                   text=r['Text']) for r in rows]\n"+
		"    out.append(lt.render_contributions(recs))\n"+
		"print(json.dumps(out))",
		string(in))
	var py []string
	decodeLT(t, raw, &py)
	if len(py) != len(renderScenarios) {
		t.Fatalf("probe returned %d rows for %d scenarios", len(py), len(renderScenarios))
	}
	for i, recs := range renderScenarios {
		if got := RenderContributions(recs); got != py[i] {
			t.Errorf("RENDER row %d (%d records)\n  go %q\n  py %q",
				i, len(recs), got, py[i])
		}
	}
	if py[0] != "" {
		t.Fatalf("the empty-list contract is broken UPSTREAM: CPython "+
			"rendered %q for zero contributions", py[0])
	}
	// A nil slice is not the same value as an empty one in Go, and only the
	// empty one is in the corpus above.
	if got := RenderContributions(nil); got != "" {
		t.Errorf("a nil slice rendered %q", got)
	}
}

// ---------------------------------------------------------------------
// ContributionLedger
// ---------------------------------------------------------------------

// One ledger operation. The corpus is a SEQUENCE per scenario rather than
// a set of one-shot calls, because the class's whole contract is about
// ordering: contributors append, the merge point drains exactly once, and
// the retry path re-arms what the failed step already saw. A per-method
// test cannot reach the 2026-07-15 clobber this class was written to stop.
type ledgerOp struct {
	Op      string      `json:"op"`
	Source  string      `json:"source"`
	Kind    string      `json:"kind"`
	Text    string      `json:"text"`
	Records [][3]string `json:"records"`
}

type ledgerScenario struct {
	ops []ledgerOp
	why string
}

func lAppend(source, kind, text string) ledgerOp {
	return ledgerOp{Op: "append", Source: source, Kind: kind, Text: text}
}
func lDrop(source string) ledgerOp { return ledgerOp{Op: "drop", Source: source} }

var (
	lDrain  = ledgerOp{Op: "drain"}
	lRender = ledgerOp{Op: "render"}
	lState  = ledgerOp{Op: "state"}
)

// The oversize fixtures are built from ASTRAL characters, so the cap is
// measured in code points and not in bytes: 32001 emoji is 128004 bytes,
// and a byte-counting port truncates at the wrong place -- mid-character,
// producing a replacement char CPython never writes.
var (
	overCap  = strings.Repeat("\U0001f600", MaxContributionTextChars+1)
	atCap    = strings.Repeat("\U0001f600", MaxContributionTextChars)
	underCap = strings.Repeat("\U0001f600", MaxContributionTextChars-1)
	// ...and the same three in ASCII, where bytes and code points agree,
	// so a failure on the astral rows alone localises the bug to counting.
	overCapASCII = strings.Repeat("x", MaxContributionTextChars+7)
)

var ledgerScenarios = []ledgerScenario{
	{[]ledgerOp{lState, lRender}, "empty: len 0, bool false, renders \"\""},

	{[]ledgerOp{
		lAppend("budget", "context", "20% left"),
		lState, lRender,
	}, "one record"},

	// DRAIN IS A CONSUME. The second drain must come back empty, and the
	// ledger must be usable afterwards.
	{[]ledgerOp{
		lAppend("a", "context", "one"),
		lAppend("b", "note", "two"),
		lDrain, lState, lDrain,
		lAppend("c", "context", "three"),
		lState,
	}, "drain, drain again, then reuse"},

	// EXTEND re-arms a drained batch verbatim -- the blocked-step retry
	// path. The records come back in their original order and are NOT
	// re-stripped or re-capped, because they already were.
	{[]ledgerOp{
		lAppend("a", "context", "one"),
		lAppend("b", "note", "two"),
		lDrain,
		{Op: "extend", Records: [][3]string{{"a", "context", "one"}, {"b", "note", "two"}}},
		lState, lRender,
	}, "drain then re-arm"},
	{[]ledgerOp{
		{Op: "extend", Records: [][3]string{{"s", "k", "   "}, {"s", "k", ""}}},
		lState, lRender,
	}, "extend does NOT filter what append would have dropped"},

	// DROP_SOURCE returns a COUNT and removes every matching record,
	// keeping the order of the rest. The merge points call it
	// unconditionally, so the zero case has to be cheap and correct.
	{[]ledgerOp{
		lAppend("time", "context", "t1"),
		lAppend("budget", "context", "b"),
		lAppend("time", "context", "t2"),
		lDrop("time"), lState, lRender,
	}, "drop two of three, order preserved"},
	{[]ledgerOp{lAppend("a", "k", "x"), lDrop("nothing"), lState},
		"dropping an absent source returns 0"},
	{[]ledgerOp{lDrop("time"), lState}, "...and works on an empty ledger"},
	{[]ledgerOp{lAppend("", "k", "x"), lDrop(""), lState},
		"the empty source is a real source"},
	{[]ledgerOp{
		lAppend("a", "k", "1"), lAppend("a", "k", "2"), lDrop("a"),
		lState, lDrain, lState,
	}, "dropping everything leaves a usable ledger"},

	// APPEND STRIPS, and Python's str.strip() covers 29 code points where
	// Go's TrimSpace covers 5. A record that is ONLY separators is
	// dropped, which is the arm that decides whether a prompt gets a
	// stray empty "[hook] " label.
	{[]ledgerOp{lAppend("s", "k", "   "), lState}, "whitespace-only is dropped"},
	{[]ledgerOp{lAppend("s", "k", ""), lState}, "empty is dropped"},
	{[]ledgerOp{lAppend("s", "k", "\u001f"), lState},
		"U+001F alone: Python strips it to empty and drops the record"},
	{[]ledgerOp{lAppend("s", "k", "\u00a0"), lState},
		"NO-BREAK SPACE alone: same"},
	{[]ledgerOp{lAppend("s", "k", "\u2028"), lState}, "U+2028 alone: same"},
	{[]ledgerOp{lAppend("s", "k", "\u001c"), lState}, "FILE SEPARATOR alone: same"},
	{[]ledgerOp{lAppend("s", "k", "\u001fx\u001f"), lState, lRender},
		"...and as PADDING it is stripped off a record that survives"},
	{[]ledgerOp{lAppend("s", "k", "\u00a0x\u00a0"), lState, lRender}, "same, NBSP"},
	{[]ledgerOp{lAppend("s", "k", "\u2028x\u0085"), lState, lRender},
		"...and the two ends can differ"},
	{[]ledgerOp{lAppend("s", "k", "  a  b  "), lState, lRender},
		"the INTERIOR is untouched"},
	{[]ledgerOp{lAppend("  s  ", "k", "x"), lState, lRender},
		"the SOURCE is not stripped -- only the text is"},

	// THE CAP. Three rows either side of it, and the marker is the
	// ledger's own -- "N chars over" counts what was REMOVED, where
	// context_budget.clip's marker states what was kept. Two different
	// announced cuts, and this is the one this class emits.
	{[]ledgerOp{lAppend("op", "note", underCap), lState}, "one under the cap"},
	{[]ledgerOp{lAppend("op", "note", atCap), lState}, "exactly at it"},
	{[]ledgerOp{lAppend("op", "note", overCap), lState},
		"one over: truncated, and the count is in CODE POINTS"},
	{[]ledgerOp{lAppend("op", "note", overCapASCII), lState},
		"...and the ASCII control, where bytes and code points agree"},
	// The strip happens BEFORE the cap, so padding does not count toward
	// it. A port that capped first would truncate a string the strip was
	// about to shorten below the cap anyway.
	{[]ledgerOp{lAppend("op", "note", "  "+atCap+"  "), lState},
		"padding around an at-cap body is stripped, NOT truncated"},

	{[]ledgerOp{
		lAppend("budget", "context", "  b  "),
		lAppend("time", "context", "t"),
		lAppend("hook", "note", "   "),
		lAppend("time", "context", "t2"),
		lDrop("time"),
		lAppend("time", "context", "t3"),
		lRender, lState, lDrain, lState, lRender,
	}, "the merge-point sequence end to end"},
}

func TestContributionLedgerMatchesCPython(t *testing.T) {
	type scenarioJSON struct {
		Ops []ledgerOp `json:"ops"`
	}
	payload := make([]scenarioJSON, 0, len(ledgerScenarios))
	for _, sc := range ledgerScenarios {
		payload = append(payload, scenarioJSON{sc.ops})
	}
	in, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	// The truncation notice is a `log.warning` on "maro.loop". Capturing it
	// through a handler is what makes the Go `warnings` slice a comparison
	// against Python's actual message rather than against a re-reading of
	// the format string.
	raw := probeLTStdin(t, ""+
		"import logging\n"+
		"import loop_types as lt\n"+
		"seen = []\n"+
		"class _Cap(logging.Handler):\n"+
		"    def emit(self, record):\n"+
		"        seen.append(record.getMessage())\n"+
		"lt.log.addHandler(_Cap())\n"+
		"lt.log.setLevel(logging.WARNING)\n"+
		"out = []\n"+
		"for sc in json.loads(sys.stdin.read()):\n"+
		"    del seen[:]\n"+
		"    led = lt.ContributionLedger()\n"+
		"    trace = []\n"+
		"    for op in sc['ops']:\n"+
		"        k = op['op']\n"+
		"        if k == 'append':\n"+
		"            led.append(op['source'], op['kind'], op['text'])\n"+
		"            trace.append(['append', None])\n"+
		"        elif k == 'extend':\n"+
		"            led.extend([lt.ContextContribution(source=r[0], kind=r[1],\n"+
		"                                               text=r[2])\n"+
		"                        for r in (op['records'] or [])])\n"+
		"            trace.append(['extend', None])\n"+
		"        elif k == 'drop':\n"+
		"            trace.append(['drop', led.drop_source(op['source'])])\n"+
		"        elif k == 'drain':\n"+
		"            trace.append(['drain', [[r.source, r.kind, r.text]\n"+
		"                                    for r in led.drain()]])\n"+
		"        elif k == 'render':\n"+
		"            trace.append(['render',\n"+
		"                          lt.render_contributions(led._pending)])\n"+
		"        elif k == 'state':\n"+
		"            trace.append(['state', [len(led), bool(led),\n"+
		"                                    [[r.source, r.kind, r.text]\n"+
		"                                     for r in led._pending]]])\n"+
		"        else:\n"+
		"            raise SystemExit('unknown op ' + k)\n"+
		"    out.append({'trace': trace, 'warnings': list(seen)})\n"+
		"print(json.dumps(out))",
		string(in))
	var py []struct {
		Trace    [][]any  `json:"trace"`
		Warnings []string `json:"warnings"`
	}
	decodeLT(t, raw, &py)
	if len(py) != len(ledgerScenarios) {
		t.Fatalf("probe returned %d rows for %d scenarios", len(py), len(ledgerScenarios))
	}

	var truncations, drops int
	for i, sc := range ledgerScenarios {
		led := NewContributionLedger()
		for j, op := range sc.ops {
			want := py[i].Trace[j]
			if want[0].(string) != op.Op {
				t.Fatalf("scenario %d op %d: the probe ran %v where the Go "+
					"side runs %q -- the two op streams have drifted",
					i, j, want[0], op.Op)
			}
			switch op.Op {
			case "append":
				led.Append(op.Source, op.Kind, op.Text)
			case "extend":
				recs := make([]ContextContribution, 0, len(op.Records))
				for _, r := range op.Records {
					recs = append(recs, ContextContribution{r[0], r[1], r[2]})
				}
				led.Extend(recs)
			case "drop":
				got := led.DropSource(op.Source)
				if float64(got) != want[1].(float64) {
					t.Errorf("scenario %d (%s) op %d drop_source(%q): go %d py %v",
						i, sc.why, j, op.Source, got, want[1])
				}
				drops++
			case "drain":
				if got, w := recsJSON(t, led.Drain()), reprAny(want[1]); got != w {
					t.Errorf("scenario %d (%s) op %d DRAIN\n  go %s\n  py %s",
						i, sc.why, j, got, w)
				}
			case "render":
				if got := RenderContributions(led.Pending()); got != want[1].(string) {
					t.Errorf("scenario %d (%s) op %d RENDER\n  go %q\n  py %q",
						i, sc.why, j, got, want[1])
				}
			case "state":
				w := want[1].([]any)
				if float64(led.Len()) != w[0].(float64) {
					t.Errorf("scenario %d (%s) op %d len: go %d py %v",
						i, sc.why, j, led.Len(), w[0])
				}
				if (led.Len() != 0) != w[1].(bool) {
					t.Errorf("scenario %d (%s) op %d bool: go %v py %v",
						i, sc.why, j, led.Len() != 0, w[1])
				}
				if got, wj := recsJSON(t, led.Pending()), reprAny(w[2]); got != wj {
					t.Errorf("scenario %d (%s) op %d PENDING\n  go %s\n  py %s",
						i, sc.why, j, got, wj)
				}
			}
		}
		if got, want := led.Warnings(), py[i].Warnings; !eqStrsLT(got, want) {
			t.Errorf("scenario %d (%s) truncation notices\n  go %v\n  py %v",
				i, sc.why, got, want)
		}
		truncations += len(py[i].Warnings)
	}
	if truncations < 2 {
		t.Fatalf("only %d truncation notices in the whole corpus; the cap "+
			"arm is barely exercised", truncations)
	}
	if drops < 4 {
		t.Fatalf("only %d drop_source calls", drops)
	}
}

// recsJSON renders records as the probe renders them -- [source, kind,
// text] triples -- so the comparison is between two lists in the same
// shape and a Go struct's field ORDER cannot silently become part of it.
func recsJSON(t *testing.T, recs []ContextContribution) string {
	t.Helper()
	rows := make([][3]string, 0, len(recs))
	for _, r := range recs {
		rows = append(rows, [3]string{r.Source, r.Kind, r.Text})
	}
	if len(rows) == 0 {
		// json.Marshal writes `null` for a nil slice and `[]` for an empty
		// one; Python always writes `[]`.
		return "[]"
	}
	return reprAny(rows)
}

// ---------------------------------------------------------------------
// LoopPhase, the transition table, and set_phase
// ---------------------------------------------------------------------

// The phase VALUES are a wire contract, not an internal enum: run_trace's
// PHASE_NODES are `"phase." + value`, and a run's trace.jsonl is read back
// by tooling that was written against those exact strings. Renaming
// PRE_FLIGHT to "preflight" here would produce a trace nothing can join.
func TestThePhaseVocabularyAndTransitionTableMatchCPython(t *testing.T) {
	raw := probeLT(t, ""+
		"import loop_types as lt\n"+
		"names = {k: v for k, v in vars(lt.LoopPhase).items()\n"+
		"         if not k.startswith('_')}\n"+
		"allowed = {k: sorted(v) for k, v in lt.LoopStateMachine._ALLOWED.items()}\n"+
		"sm = lt.LoopStateMachine()\n"+
		"try:\n"+
		"    sm.set_phase(lt.LoopPhase.EXECUTE)\n"+
		"    msg = ''\n"+
		"except lt.InvalidTransitionError as e:\n"+
		"    msg = str(e)\n"+
		"print(json.dumps({'names': names, 'allowed': allowed,\n"+
		"                  'msg': msg, 'start': sm.phase}))")
	var py struct {
		Names   map[string]string   `json:"names"`
		Allowed map[string][]string `json:"allowed"`
		Msg     string              `json:"msg"`
		Start   string              `json:"start"`
	}
	decodeLT(t, raw, &py)

	goNames := map[string]string{
		"INIT": PhaseInit, "DECOMPOSE": PhaseDecompose,
		"PRE_FLIGHT": PhasePreFlight, "PARALLEL": PhaseParallel,
		"PREPARE": PhasePrepare, "EXECUTE": PhaseExecute,
		"FINALIZE": PhaseFinalize,
	}
	if len(goNames) != len(py.Names) {
		t.Fatalf("LoopPhase has %d names in CPython and %d here: %v vs %v",
			len(py.Names), len(goNames), sortedStrs(keysOfStr(py.Names)),
			sortedStrs(keysOfStr(goNames)))
	}
	for k, want := range py.Names {
		if got, ok := goNames[k]; !ok || got != want {
			t.Errorf("LoopPhase.%s: go %q py %q", k, got, want)
		}
	}

	// The table, both directions. FINALIZE's entry is an EMPTY set and not
	// an absent key; the difference is invisible through AllowedFrom (both
	// answer nothing) and visible in the table, so the table is what is
	// compared.
	if len(py.Allowed) != len(allowedTransitions) {
		t.Errorf("the transition table has %d rows in CPython and %d here",
			len(py.Allowed), len(allowedTransitions))
	}
	for phase, want := range py.Allowed {
		row, ok := allowedTransitions[phase]
		if !ok {
			t.Errorf("_ALLOWED has a row for %q and this port has none", phase)
			continue
		}
		got := sortedStrs(keysOfBool(row))
		if reprAny(got) != reprAny(want) {
			t.Errorf("_ALLOWED[%q]\n  go %v\n  py %v", phase, got, want)
		}
		// ...and AllowedFrom has to agree with the table it reads.
		if reprAny(sortedStrs(keysOfBool(AllowedFrom(phase)))) != reprAny(want) {
			t.Errorf("AllowedFrom(%q) disagrees with allowedTransitions", phase)
		}
	}
	for phase := range allowedTransitions {
		if _, ok := py.Allowed[phase]; !ok {
			t.Errorf("this port has a row for %q and _ALLOWED does not", phase)
		}
	}
	// An unknown phase is `_ALLOWED.get(phase, set())`, which is the same
	// empty answer FINALIZE gives.
	if len(AllowedFrom("no-such-phase")) != 0 {
		t.Error("an unknown phase answered something")
	}
	// AllowedFrom returns a COPY: a caller that mutates the answer must not
	// reach the table. Python returns the set object itself, so this is a
	// deliberate divergence in the safer direction and it is worth knowing
	// it holds.
	m := AllowedFrom(PhaseInit)
	m["injected"] = true
	if allowedTransitions[PhaseInit]["injected"] {
		t.Error("AllowedFrom handed out the real table")
	}

	if py.Start != PhaseInit {
		t.Errorf("a fresh LoopStateMachine starts at %q there and %q here",
			py.Start, PhaseInit)
	}
	// The message reaches logs and is caught by type elsewhere, so both are
	// contract -- including the U+2192 arrow, which is not an ASCII "->".
	if py.Msg == "" {
		t.Fatal("CPython allowed INIT -> EXECUTE, so the premise has moved")
	}
	got := (&InvalidTransitionError{From: PhaseInit, To: PhaseExecute}).Error()
	if got != py.Msg {
		t.Errorf("the InvalidTransitionError message\n  go %q\n  py %q", got, py.Msg)
	}
}

func keysOfStr(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

func keysOfBool(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

func sortedStrs(s []string) []string {
	sort.Strings(s)
	return s
}

// set_phase walks the machine AND records the edge, and the two are not
// independent: the phase is mutated before the record, so a recorder that
// read the context would see the new phase, and `prior` is captured before
// that. The walks below are driven through both runtimes with the recorder
// stubbed, so the comparison covers the edge STRINGS as well as the
// accept/reject decision.
var phaseWalks = [][]string{
	{"decompose", "pre_flight", "prepare", "execute", "finalize"},
	{"decompose", "pre_flight", "parallel", "prepare", "execute", "finalize"},
	{"finalize"},
	{"decompose", "finalize"},
	// Rejections. The first is a skipped rung, the second a backwards
	// step, the third a self-transition, the fourth an unknown phase --
	// and after each the machine must be UNMOVED, which the next legal
	// step in the same walk proves.
	{"execute", "decompose"},
	{"decompose", "init", "pre_flight"},
	{"decompose", "decompose", "pre_flight"},
	{"nonsense", "decompose"},
	{"decompose", "pre_flight", "prepare", "execute", "finalize", "decompose"},
	{""},
}

func TestSetPhaseMatchesCPython(t *testing.T) {
	in, err := json.Marshal(phaseWalks)
	if err != nil {
		t.Fatal(err)
	}
	// run_trace is STUBBED. The real one writes trace.jsonl into the live
	// workspace, and a differential that writes to ~/.maro is a
	// differential that damages the thing it is measuring.
	raw := probeLT(t, ""+
		"import types\n"+
		"stub = types.ModuleType('run_trace')\n"+
		"edges = []\n"+
		"def record_edge(a, b, loop_id=None, **kw):\n"+
		"    edges.append([a, b, loop_id])\n"+
		"stub.record_edge = record_edge\n"+
		"sys.modules['run_trace'] = stub\n"+
		"import loop_types as lt\n"+
		"out = []\n"+
		"for walk in json.loads(sys.argv[2]):\n"+
		"    del edges[:]\n"+
		"    sm = lt.LoopStateMachine(loop_id='L1')\n"+
		"    steps = []\n"+
		"    for target in walk:\n"+
		"        try:\n"+
		"            sm.set_phase(target)\n"+
		"            steps.append([target, '', sm.phase])\n"+
		"        except lt.InvalidTransitionError as e:\n"+
		"            steps.append([target, str(e), sm.phase])\n"+
		"    out.append({'steps': steps, 'edges': list(edges)})\n"+
		"print(json.dumps(out))",
		string(in))
	var py []struct {
		Steps [][3]string `json:"steps"`
		Edges [][3]any    `json:"edges"`
	}
	decodeLT(t, raw, &py)
	if len(py) != len(phaseWalks) {
		t.Fatalf("probe returned %d walks for %d", len(py), len(phaseWalks))
	}

	var rejects int
	for i, walk := range phaseWalks {
		sm := NewLoopStateMachine()
		sm.LoopID = "L1"
		// Not `var edges [][3]any`: a nil slice marshals to `null` and
		// Python's empty list to `[]`, so the walk that records no edge at
		// all would report a difference the runtimes do not have.
		edges := [][3]any{}
		trace := func(from, to, loopID string) {
			edges = append(edges, [3]any{from, to, loopID})
		}
		for j, target := range walk {
			err := sm.SetPhase(target, trace)
			msg := ""
			if err != nil {
				msg = err.Error()
				rejects++
				var ite *InvalidTransitionError
				if !errors.As(err, &ite) {
					t.Errorf("walk %d step %d returned a %T, and callers "+
						"catch InvalidTransitionError by TYPE", i, j, err)
				}
			}
			want := py[i].Steps[j]
			if msg != want[1] {
				t.Errorf("walk %d step %d (-> %q) error\n  go %q\n  py %q",
					i, j, target, msg, want[1])
			}
			if sm.Phase != want[2] {
				t.Errorf("walk %d step %d (-> %q) landed on %q; CPython is at %q",
					i, j, target, sm.Phase, want[2])
			}
		}
		if reprAny(edges) != reprAny(py[i].Edges) {
			t.Errorf("walk %d recorded different EDGES -- this is what a run's "+
				"phase track is built from\n  go %s\n  py %s",
				i, reprAny(edges), reprAny(py[i].Edges))
		}
	}
	if rejects < 5 {
		t.Fatalf("only %d rejected transitions in the corpus", rejects)
	}

	// THE ORDERING. The phase is mutated BEFORE the edge is recorded, so a
	// recorder that reads the context sees the NEW phase -- and `prior` was
	// captured before that, so the edge still names where the run came
	// from. Nothing in the earlier walks can see this: the TraceFunc is
	// handed strings, and a closure that ignores the context cannot tell
	// the two orderings apart. Python's recorder can read it, so this
	// drives the same question through both.
	rawOrder := probeLT(t, ""+
		"import types\n"+
		"stub = types.ModuleType('run_trace')\n"+
		"seen = {}\n"+
		"holder = {}\n"+
		"def record_edge(a, b, loop_id=None, **kw):\n"+
		"    seen['phase'] = holder['sm'].phase\n"+
		"    seen['edge'] = [a, b]\n"+
		"stub.record_edge = record_edge\n"+
		"sys.modules['run_trace'] = stub\n"+
		"import loop_types as lt\n"+
		"sm = lt.LoopStateMachine()\n"+
		"holder['sm'] = sm\n"+
		"sm.set_phase(lt.LoopPhase.DECOMPOSE)\n"+
		"print(json.dumps([seen['phase'], seen['edge']]))")
	var order []any
	decodeLT(t, rawOrder, &order)

	obs := NewLoopStateMachine()
	var phaseAtRecord string
	var edgeAtRecord []string
	if err := obs.SetPhase(PhaseDecompose, func(from, to, _ string) {
		phaseAtRecord = obs.Phase
		edgeAtRecord = []string{from, to}
	}); err != nil {
		t.Fatal(err)
	}
	if phaseAtRecord != order[0].(string) {
		t.Errorf("a recorder reading the context saw phase %q here and %q "+
			"in CPython: the mutate/record ORDER differs",
			phaseAtRecord, order[0])
	}
	if reprAny(edgeAtRecord) != reprAny(order[1]) {
		t.Errorf("the edge passed to the recorder\n  go %s\n  py %s",
			reprAny(edgeAtRecord), reprAny(order[1]))
	}

	// A nil recorder is Python's failed `from run_trace import record_edge`,
	// and a panicking one is its bare `except Exception: pass`. Neither may
	// fail the transition -- the trace is an observation of the walk, never
	// a gate on it.
	sm := NewLoopStateMachine()
	if err := sm.SetPhase(PhaseDecompose, nil); err != nil {
		t.Errorf("a nil recorder failed the transition: %v", err)
	}
	if sm.Phase != PhaseDecompose {
		t.Errorf("a nil recorder left the phase at %q", sm.Phase)
	}
	if err := sm.SetPhase(PhasePreFlight, func(_, _, _ string) {
		panic("the trace writer died")
	}); err != nil {
		t.Errorf("a panicking recorder failed the transition: %v", err)
	}
	if sm.Phase != PhasePreFlight {
		t.Errorf("a panicking recorder left the phase at %q", sm.Phase)
	}
}

// ---------------------------------------------------------------------
// stamp_stop / stamp_pause
// ---------------------------------------------------------------------

type stampOp struct {
	Kind     string `json:"kind"`
	Verdict  string `json:"verdict"`
	Evidence string `json:"evidence"`
}

var longEvidence = strings.Repeat("\U0001f600", 900)
var longEvidenceASCII = strings.Repeat("e", 900)

var stampScenarios = []struct {
	ops []stampOp
	why string
}{
	{nil, "unstamped"},
	{[]stampOp{{"stop", "out-of-budget", "spent $12 of $10"}}, "the ordinary stamp"},
	{[]stampOp{{"stop", "out-of-budget", ""}}, "no evidence"},

	// FIRST WRITE WINS. The break site knows the cause; finalize must not
	// overwrite it with a generic one. The second stamp is also silent --
	// it is not an error and it logs nothing.
	{[]stampOp{
		{"stop", "out-of-budget", "first"},
		{"stop", "thesis-refuted", "second"},
	}, "the second stamp is ignored, evidence included"},

	// OFF-VOCABULARY IS DROPPED, not stored: a typo'd verdict persisting
	// silently would drift past every string-matching consumer. And
	// because it is dropped, a LATER valid stamp still lands.
	{[]stampOp{{"stop", "out-of-budgt", "typo"}}, "a near-miss verdict"},
	{[]stampOp{{"stop", "", "x"}}, "the empty verdict"},
	{[]stampOp{{"stop", "OUT-OF-BUDGET", "x"}}, "the vocabulary is case-sensitive"},
	{[]stampOp{{"stop", " out-of-budget", "x"}}, "...and is not stripped"},
	{[]stampOp{
		{"stop", "nonsense", "dropped"},
		{"stop", "out-of-budget", "kept"},
	}, "a dropped stamp does not consume the first-write"},

	// The evidence is CLIPPED at 800 by context_budget.clip, which
	// announces what it removed. Astral characters make the count code
	// points rather than bytes.
	{[]stampOp{{"stop", "out-of-budget", longEvidence}}, "900 astral chars"},
	{[]stampOp{{"stop", "out-of-budget", longEvidenceASCII}}, "900 ASCII chars"},
	{[]stampOp{{"stop", "out-of-budget", strings.Repeat("e", 800)}}, "exactly 800"},
	{[]stampOp{{"stop", "out-of-budget", strings.Repeat("e", 801)}}, "801"},

	// stamp_pause has the same first-write and vocabulary contract, and
	// deliberately NO clip -- the reason is a vocabulary member, not free
	// text, and there is no evidence field beside it.
	{[]stampOp{{"pause", "awaiting-clarification", ""}}, "pause"},
	{[]stampOp{
		{"pause", "awaiting-clarification", ""},
		{"pause", "llm-unreachable", ""},
	}, "pause first-write-wins"},
	{[]stampOp{{"pause", "not-a-reason", ""}}, "an off-vocabulary reason"},
	{[]stampOp{
		{"pause", "not-a-reason", ""},
		{"pause", "awaiting-clarification", ""},
	}, "...does not consume the first-write either"},

	// The two stamps are INDEPENDENT: a run can be both stopped and
	// paused, and neither vocabulary bleeds into the other.
	{[]stampOp{
		{"stop", "out-of-budget", "e"},
		{"pause", "awaiting-clarification", ""},
	}, "both"},
	{[]stampOp{{"pause", "out-of-budget", ""}}, "a STOP value is not a PAUSE reason"},
	{[]stampOp{{"stop", "awaiting-clarification", ""}}, "...and the reverse"},
}

func TestStampStopAndStampPauseMatchCPython(t *testing.T) {
	type sJSON struct {
		Ops []stampOp `json:"ops"`
	}
	payload := make([]sJSON, 0, len(stampScenarios))
	for _, sc := range stampScenarios {
		payload = append(payload, sJSON{sc.ops})
	}
	in, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	raw := probeLTStdin(t, ""+
		"import logging\n"+
		"import loop_types as lt\n"+
		"seen = []\n"+
		"class _Cap(logging.Handler):\n"+
		"    def emit(self, record):\n"+
		"        seen.append(record.getMessage())\n"+
		"cap = _Cap()\n"+
		"logging.getLogger('maro.loop').addHandler(cap)\n"+
		"logging.getLogger('maro.loop').setLevel(logging.WARNING)\n"+
		"out = []\n"+
		"for sc in json.loads(sys.stdin.read()):\n"+
		"    del seen[:]\n"+
		"    ctx = lt.LoopContext()\n"+
		"    for op in (sc['ops'] or []):\n"+
		"        if op['kind'] == 'stop':\n"+
		"            ctx.stamp_stop(op['verdict'], op['evidence'])\n"+
		"        else:\n"+
		"            ctx.stamp_pause(op['verdict'])\n"+
		"    out.append({'verdict': ctx.stop_verdict,\n"+
		"                'evidence': ctx.stop_evidence,\n"+
		"                'pause': ctx.pause_reason,\n"+
		"                'warnings': list(seen)})\n"+
		"print(json.dumps(out))",
		string(in))
	var py []struct {
		Verdict  string   `json:"verdict"`
		Evidence string   `json:"evidence"`
		Pause    string   `json:"pause"`
		Warnings []string `json:"warnings"`
	}
	decodeLT(t, raw, &py)
	if len(py) != len(stampScenarios) {
		t.Fatalf("probe returned %d rows for %d scenarios", len(py), len(stampScenarios))
	}

	var clipped, dropped int
	for i, sc := range stampScenarios {
		ctx := NewLoopContext()
		for _, op := range sc.ops {
			if op.Kind == "stop" {
				ctx.StampStop(op.Verdict, op.Evidence)
			} else {
				ctx.StampPause(op.Verdict)
			}
		}
		w := py[i]
		if ctx.StopVerdict != w.Verdict {
			t.Errorf("row %d (%s) stop_verdict: go %q py %q",
				i, sc.why, ctx.StopVerdict, w.Verdict)
		}
		if ctx.StopEvidence != w.Evidence {
			t.Errorf("row %d (%s) stop_evidence\n  go %q\n  py %q",
				i, sc.why, clipTail(ctx.StopEvidence), clipTail(w.Evidence))
		}
		if ctx.PauseReason != w.Pause {
			t.Errorf("row %d (%s) pause_reason: go %q py %q",
				i, sc.why, ctx.PauseReason, w.Pause)
		}
		if got := ctx.Warnings(); !eqStrsLT(got, w.Warnings) {
			t.Errorf("row %d (%s) off-vocabulary notices\n  go %v\n  py %v",
				i, sc.why, got, w.Warnings)
		}
		for _, op := range sc.ops {
			if len([]rune(op.Evidence)) > 800 {
				clipped++
			}
		}
		dropped += len(w.Warnings)
	}
	// StampPause deliberately does NOT clip, and the reason that cannot
	// matter is HEADROOM: every reason that gets past the vocabulary gate
	// is far shorter than the 800 StampStop clips its evidence at. The
	// comment at the site says so; this is what checks it.
	rawVocab := probeLT(t, ""+
		"import stop_verdicts as sv\n"+
		"print(json.dumps(sorted(sv.VALID_PAUSE_REASONS)))")
	var reasons []string
	decodeLT(t, rawVocab, &reasons)
	if len(reasons) == 0 {
		t.Fatal("the pause vocabulary is empty")
	}
	for _, r := range reasons {
		if len([]rune(r)) >= 800 {
			t.Errorf("the pause reason %q is %d chars, which is past the "+
				"clip StampStop uses -- the 'no clip needed' claim at the "+
				"site no longer holds", r, len([]rune(r)))
		}
	}

	if clipped < 2 || dropped < 4 {
		t.Fatalf("the corpus reaches %d over-cap evidences and %d dropped "+
			"stamps", clipped, dropped)
	}
}

// clipTail keeps a failure message readable when the value is 900 astral
// characters: the interesting end of a clipped string is where the cut is.
func clipTail(s string) string {
	r := []rune(s)
	if len(r) <= 90 {
		return string(r)
	}
	return string(r[:40]) + " ...[" + strconv.Itoa(len(r)) + " chars]... " +
		string(r[len(r)-40:])
}

// ---------------------------------------------------------------------
// The dataclass field sets and their defaults
// ---------------------------------------------------------------------

// snakeLT is the Go-name -> Python-name rule, applied mechanically rather
// than through a hand-written table of 64 entries. A table is an
// enumeration, and an enumeration can be wrong at BIRTH -- the same way a
// transcribed attribute table was, two tests up this file.
func snakeLT(name string) string {
	rs := []rune(name)
	var out []rune
	for i, r := range rs {
		if r < 'A' || r > 'Z' {
			out = append(out, r)
			continue
		}
		prevLower := i > 0 && (rs[i-1] < 'A' || rs[i-1] > 'Z')
		nextLower := i+1 < len(rs) && (rs[i+1] < 'A' || rs[i+1] > 'Z')
		if i > 0 && (prevLower || nextLower) {
			out = append(out, '_')
		}
		out = append(out, r+('a'-'A'))
	}
	return string(out)
}

// classify renders one Go value in the vocabulary the probe renders Python
// values in, so a default can be compared across two type systems without
// either side's spelling leaking into the comparison.
//
// The `obj` tag carries no value: CPython's type name for a
// ContributionLedger is not Go's, and demanding they match would be
// asserting a coincidence. What IS asserted is that both sides think the
// field holds an object rather than None -- which is the whole content of
// "these fields have a default_factory".
func classify(v reflect.Value) [2]any {
	switch v.Kind() {
	case reflect.String:
		return [2]any{"str", v.String()}
	case reflect.Bool:
		return [2]any{"bool", v.Bool()}
	case reflect.Int, reflect.Int64:
		return [2]any{"int", v.Int()}
	case reflect.Float64:
		return [2]any{"float", v.Float()}
	case reflect.Slice:
		return [2]any{"list", v.Len()}
	case reflect.Map:
		// A Go set is map[T]bool; a Go dict is anything else. Python
		// distinguishes them by type, and milestone_expanded is the one
		// set in this struct.
		if v.Type().Elem().Kind() == reflect.Bool {
			return [2]any{"set", v.Len()}
		}
		return [2]any{"dict", v.Len()}
	case reflect.Ptr, reflect.Interface, reflect.Func:
		if v.IsNil() {
			return [2]any{"none", nil}
		}
		return [2]any{"obj", nil}
	}
	return [2]any{"?" + v.Kind().String(), nil}
}

const classifyPy = "" +
	"def classify(v):\n" +
	"    if v is None: return ['none', None]\n" +
	"    if isinstance(v, bool): return ['bool', v]\n" +
	"    if isinstance(v, int): return ['int', v]\n" +
	"    if isinstance(v, float): return ['float', v]\n" +
	"    if isinstance(v, str): return ['str', v]\n" +
	"    if isinstance(v, set): return ['set', len(v)]\n" +
	"    if isinstance(v, dict): return ['dict', len(v)]\n" +
	"    if isinstance(v, (list, tuple)): return ['list', len(v)]\n" +
	"    return ['obj', None]\n"

// Two things are checked at once and they are different claims. The FIELD
// SET says nothing was dropped in the port -- a field added to the Python
// dataclass and not here is a piece of run state that silently stops
// travelling. The DEFAULTS say a freshly built context starts in the same
// state, which matters most where the default is not the zero value:
// loop_status "done", phase "init", max_iterations 40,
// director_budget_ceiling 3, audit_learning_allowed True.
func TestTheContextAndResultFieldsAndDefaultsMatchCPython(t *testing.T) {
	raw := probeLT(t, ""+
		"import dataclasses\n"+
		"import loop_types as lt\n"+
		classifyPy+
		"def dump(obj):\n"+
		"    return [[f.name, classify(getattr(obj, f.name))]\n"+
		"            for f in dataclasses.fields(obj)]\n"+
		"print(json.dumps({'ctx': dump(lt.LoopContext()),\n"+
		"                  'res': dump(lt.LoopResult('', '', '', ''))}))")
	var py struct {
		Ctx [][2]json.RawMessage `json:"ctx"`
		Res [][2]json.RawMessage `json:"res"`
	}
	decodeLT(t, raw, &py)

	for _, tc := range []struct {
		label string
		rows  [][2]json.RawMessage
		val   reflect.Value
	}{
		{"LoopContext", py.Ctx, reflect.ValueOf(*NewLoopContext())},
		{"LoopResult", py.Res, reflect.ValueOf(*NewLoopResult("", "", "", ""))},
	} {
		// Unexported fields are this port's own bookkeeping (the warnings
		// slice standing in for Python's logger) and have no counterpart.
		var goFields []reflect.StructField
		tt := tc.val.Type()
		for i := 0; i < tt.NumField(); i++ {
			if tt.Field(i).PkgPath == "" {
				goFields = append(goFields, tt.Field(i))
			}
		}
		if len(goFields) != len(tc.rows) {
			t.Errorf("%s has %d fields in CPython and %d exported here",
				tc.label, len(tc.rows), len(goFields))
		}
		pyByName := map[string][2]json.RawMessage{}
		var pyOrder []string
		for _, row := range tc.rows {
			var name string
			if err := json.Unmarshal(row[0], &name); err != nil {
				t.Fatal(err)
			}
			pyByName[name] = row
			pyOrder = append(pyOrder, name)
		}
		var goOrder []string
		for _, f := range goFields {
			name := snakeLT(f.Name)
			goOrder = append(goOrder, name)
			row, ok := pyByName[name]
			if !ok {
				t.Errorf("%s.%s maps to %q, which is not a field of the "+
					"dataclass. Either the port invented it or the name rule "+
					"does not cover it", tc.label, f.Name, name)
				continue
			}
			delete(pyByName, name)
			got := classify(tc.val.FieldByIndex(f.Index))
			if reprAny(got) != string(mustCompact(t, row[1])) {
				t.Errorf("%s.%s DEFAULT\n  go %s\n  py %s",
					tc.label, name, reprAny(got), row[1])
			}
		}
		for name := range pyByName {
			t.Errorf("%s.%s exists in the dataclass and NOT in this port: a "+
				"piece of run state that stops travelling", tc.label, name)
		}
		// Field ORDER is not a contract in either language, but a drift in
		// it is a strong hint that one side was edited without the other,
		// so it is reported rather than asserted.
		if len(goOrder) == len(pyOrder) && reprAny(goOrder) != reprAny(pyOrder) {
			t.Logf("%s: the field ORDER differs (harmless, but the two files "+
				"have drifted)\n  go %v\n  py %v", tc.label, goOrder, pyOrder)
		}
	}
}

func mustCompact(t *testing.T, raw json.RawMessage) []byte {
	t.Helper()
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatal(err)
	}
	return []byte(reprAny(v))
}

// The default_factory fields must be FRESH per instance. Python's hazard is
// a mutable class-level default; Go's is a package-level var used as the
// default. Different spellings, same accumulator shared by every run in the
// process -- which for terrain (hosts observed to block THIS run) and
// world_facts (findings declared by THIS run) would leak one run's state
// into the next.
func TestTheFactoryDefaultsAreFreshPerContext(t *testing.T) {
	a, b := NewLoopContext(), NewLoopContext()
	if a.PendingContext == b.PendingContext {
		t.Error("two contexts share one ContributionLedger")
	}
	if a.Terrain == b.Terrain {
		t.Error("two contexts share one TerrainMemory")
	}
	if a.WorldFacts == b.WorldFacts {
		t.Error("two contexts share one WorldFactLedger")
	}
	a.PendingContext.Append("s", "k", "x")
	a.StepRetries["step-1"] = 2
	a.MilestoneExpanded[1] = true
	a.StepTierOverrides["step-1"] = "power"
	if b.PendingContext.Len() != 0 || len(b.StepRetries) != 0 ||
		len(b.MilestoneExpanded) != 0 || len(b.StepTierOverrides) != 0 {
		t.Error("a write to one context reached another")
	}
	// NewLoopStateMachine has to go through the same factory rather than
	// wrapping a zero LoopContext -- a nil map panics on write, and a nil
	// PendingContext panics on Append.
	sm := NewLoopStateMachine()
	if sm.PendingContext == nil || sm.StepRetries == nil ||
		sm.Terrain == nil || sm.WorldFacts == nil || sm.MilestoneExpanded == nil ||
		sm.StepTierOverrides == nil {
		t.Fatal("NewLoopStateMachine skipped the factories")
	}
	if sm.Phase != PhaseInit || sm.LoopStatus != "done" ||
		sm.MaxIterations != 40 || sm.DirectorBudgetCeiling != MaxRestartDepth {
		t.Error("NewLoopStateMachine skipped the non-zero scalar defaults")
	}
}

// Every rune whose pytext.Upper differs from strings.ToUpper AND whose
// expansion lands inside a level name is a rune that can flip this ladder,
// and each one needs a fixture. The set is SWEPT rather than reasoned
// about: the comment that used to sit on ResolveLogLevel argued from the
// supplement's shape that no such rune existed, was wrong (U+FB05 and
// U+FB06 render "ST", which is inside the `_STYLES` entry added the same
// afternoon), and had already survived a mutation of the call site.
//
// Both functions render a string rune by rune, so if two spellings of some
// s differ then some rune renders differently, and for either spelling to
// equal a name N that rune's rendering must be a contiguous substring of
// N. Sweeping single runes therefore finds every rune that could matter.
//
// This is the lens closed rather than re-found: a new entry in either
// table, or a new expansion in pytext's supplement, fails here on the day
// it lands instead of waiting for someone to think of the case.
func TestTheUpperExpansionsThatCanReachALevelNameAreAllFixtured(t *testing.T) {
	names := make([]string, 0, len(loggingIntAttrs)+len(loggingOtherAttrs))
	for n := range loggingIntAttrs {
		names = append(names, n)
	}
	for n := range loggingOtherAttrs {
		names = append(names, n)
	}
	if len(names) < 8 {
		t.Fatalf("only %d names to check against", len(names))
	}
	inSomeName := func(s string) string {
		if s == "" {
			return ""
		}
		for _, n := range names {
			if strings.Contains(n, s) {
				return n
			}
		}
		return ""
	}

	var differing int
	var reachable []rune
	for r := rune(0); r <= 0x10FFFF; r++ {
		if r >= 0xD800 && r <= 0xDFFF {
			continue // not a rune a Go string can carry
		}
		s := string(r)
		py, std := pytext.Upper(s), strings.ToUpper(s)
		if py == std {
			continue
		}
		differing++
		if inSomeName(py) != "" || inSomeName(std) != "" {
			reachable = append(reachable, r)
		}
	}
	if differing < 100 {
		t.Fatalf("only %d runes render differently under the two functions; "+
			"the supplement has been emptied and this sweep no longer "+
			"measures anything", differing)
	}

	for _, r := range reachable {
		var covered bool
		for _, sc := range logScenarios {
			if strings.ContainsRune(sc.Level, r) {
				covered = true
				break
			}
		}
		if !covered {
			t.Errorf("U+%04X upper-cases to %q, which is inside the level "+
				"name %q -- so it can flip this ladder, and no row of "+
				"logScenarios carries it. Add one; the differential is what "+
				"says which way it flips", r, pytext.Upper(string(r)),
				inSomeName(pytext.Upper(string(r))))
		}
	}
	// ...and the sweep has to still be FINDING something. If the tables
	// ever lose every name an expansion can reach, the loop above passes
	// vacuously and the fixtures it guards become unmotivated.
	if len(reachable) == 0 {
		t.Log("no expansion reaches any level name any more: the ligature " +
			"rows in logScenarios are now testing nothing, and the " +
			"pytext.Upper call at the site is no longer load-bearing")
	}
}
