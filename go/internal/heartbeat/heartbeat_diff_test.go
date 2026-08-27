package heartbeat

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// hbPySrc drives the real heartbeat.py. It is the same probe that captured
// this chunk's ground truth BEFORE any Go existed (scratchpad/hb_probe.py),
// carried here verbatim rather than paraphrased — the ground-truth pass and
// the differential must not be able to drift apart.
//
// `checks` arrives as a LIST OF PAIRS, not an object. A Go map marshals in
// random order and a JSON object's order is not something json.Unmarshal
// preserves on the Python side either; check order is observable in two
// rendered strings here, so the fixture carries it explicitly.
const hbPySrc = `
import json, sys
import heartbeat as hb

def _checks(pairs):
    return {k: v for k, v in pairs}

def _actions(spec):
    return [hb.RecoveryAction(**a) for a in spec]

def _report(c):
    return hb.HeartbeatReport(
        run_id=c.get("run_id", "r"), checked_at=c.get("checked_at", "t"),
        health_status=c["health_status"], checks=_checks(c["checks"]),
        stuck_projects=c["stuck_projects"],
        recovery_actions=_actions(c["actions"]),
        telegram_sent=c.get("telegram_sent", False),
        elapsed_ms=c.get("elapsed_ms", 0),
        quality_summary=c.get("quality_summary", ""))

out = []
for c in json.loads(sys.argv[1]):
    try:
        k = c["kind"]
        if k == "tier1":
            acts = hb._tier1_scripted(_checks(c["checks"]))
            out.append({"ok": [{"tier": a.tier, "target": a.target,
                                "action": a.action, "outcome": a.outcome,
                                "detail": a.detail} for a in acts]})
        elif k == "report":
            r = _report(c)
            out.append({"ok": {"summary": r.summary(),
                               "to_dict": json.dumps(r.to_dict()),
                               "keys": list(r.to_dict().keys())}})
        elif k == "escalate":
            r = _report(c)
            sent = {}
            def fake(msg):
                sent["msg"] = msg
                if c.get("raises"):
                    raise RuntimeError(c["raises"])
                return c.get("returns", True)
            hb.telegram_notify = fake
            got = hb._tier3_escalate(r)
            out.append({"ok": {"returned": got, "message": sent.get("msg"),
                               "called": "msg" in sent}})
        elif k == "shadow_every":
            out.append({"ok": hb._resolve_shadow_every(c["v"])})
        elif k == "backlog_every":
            out.append({"ok": hb._resolve_backlog_every(c["v"])})
        else:
            out.append({"err": "unknown kind " + k})
    except Exception as e:
        out.append({"err": type(e).__name__ + ": " + str(e)})
print(json.dumps(out))
`

// pair is one ordered check entry. Val is any because a non-string detail
// is a case, not an accident.
type pair struct {
	Key string
	Val any
}

func (p pair) MarshalJSON() ([]byte, error) {
	return json.Marshal([]any{p.Key, p.Val})
}

type hbCase struct {
	name string
	spec map[string]any
	// run produces the Go answer for this case in the same shape the probe
	// produces the Python one: a value, or an error whose string is
	// "Class: message".
	run func() (any, error)
}

func checksOf(pairs []pair) pyval.Obj {
	o := pyval.Obj{}
	for _, p := range pairs {
		o.Set(p.Key, p.Val)
	}
	return o
}

// pyErrText renders a Go error the way the probe renders a Python one.
// A *PyErr carries its class in a field (Error() is the message alone, so
// that a logged `%s` matches CPython's); this is the one place the class is
// wanted, because the class is half of what the differential compares.
func pyErrText(err error) string {
	var pe *pyval.PyErr
	if errors.As(err, &pe) {
		return pe.Class + ": " + pe.Msg
	}
	return "<non-PyErr>: " + err.Error()
}

// goActions renders []RecoveryAction as the probe renders Python's.
func goActions(as []RecoveryAction) any {
	out := []any{}
	for _, a := range as {
		out = append(out, map[string]any{
			"tier": float64(a.Tier), "target": a.Target, "action": a.Action,
			"outcome": a.Outcome, "detail": a.Detail,
		})
	}
	return out
}

func hbCases() []hbCase {
	var cs []hbCase

	tier1 := func(name string, pairs ...pair) {
		if pairs == nil {
			// A nil variadic marshals to JSON null, and the probe's dict
			// comprehension then fails on None rather than building an
			// empty dict — a fixture artifact that reads exactly like a
			// port bug.
			pairs = []pair{}
		}
		cs = append(cs, hbCase{
			name: name,
			spec: map[string]any{"kind": "tier1", "checks": pairs},
			run: func() (any, error) {
				acts, err := Tier1Scripted(checksOf(pairs))
				if err != nil {
					return nil, err
				}
				return goActions(acts), nil
			},
		})
	}

	// --- _tier1_scripted ---------------------------------------------------
	tier1("T1 the four known names, in dict insertion order",
		pair{"workspace_writable", "fail: read-only"},
		pair{"disk_space", "warn: 92% used"},
		pair{"llm_backend", "fail: no lane"},
		pair{"openclaw_gateway", "fail: refused"})
	tier1("T2 a failing check with no rule produces NOTHING",
		pair{"some_new_check", "fail: whatever"})
	tier1("T3 ok checks produce nothing", pair{"disk_space", "ok: 12% used"})
	tier1("T4 startswith means failure/failed/warning all match",
		pair{"disk_space", "failure"}, pair{"llm_backend", "warning: slow"})
	tier1("T5 a bare 'fail' with no detail still fires",
		pair{"disk_space", "fail"})
	tier1("T6 case matters: FAIL does not match",
		pair{"disk_space", "FAIL: shouting"})
	tier1("T7 a lone warn", pair{"disk_space", "warn: x"})
	tier1("T8 an empty checks map")
	// The whole point of P-ordering: reversing the INPUT reverses the OUTPUT.
	// A port that walked tier1Rules instead would pass T1 and fail this.
	tier1("T9 reversed insertion order reverses the actions",
		pair{"openclaw_gateway", "fail: a"}, pair{"llm_backend", "fail: b"},
		pair{"disk_space", "fail: c"}, pair{"workspace_writable", "fail: d"})
	// A non-string detail raises — and raises even when the name has no
	// rule, because startswith runs before the name is consulted.
	tier1("T10 an int detail raises AttributeError",
		pair{"disk_space", 5})
	tier1("T11 a null detail raises AttributeError",
		pair{"disk_space", nil})
	tier1("T12 an int detail on an UNKNOWN name still raises",
		pair{"some_new_check", 5})
	// The raise happens mid-walk, so the actions accumulated before it are
	// discarded rather than returned.
	tier1("T13 a good check before a bad one still raises",
		pair{"disk_space", "fail: real"}, pair{"llm_backend", 7})

	// --- HeartbeatReport ---------------------------------------------------
	report := func(name string, r Report, extra map[string]any) {
		spec := map[string]any{
			"kind": "report", "run_id": r.RunID, "checked_at": r.CheckedAt,
			"health_status": r.HealthStatus, "checks": objPairs(r.Checks),
			"stuck_projects": strList(r.StuckProjects),
			"actions":        actionSpecs(r.RecoveryActions),
			"telegram_sent":  r.TelegramSent, "elapsed_ms": r.ElapsedMS,
			"quality_summary": r.QualitySummary,
		}
		for k, v := range extra {
			spec[k] = v
		}
		cs = append(cs, hbCase{name: name, spec: spec, run: func() (any, error) {
			raw, err := pyval.DumpsCompactPy(r.ToDict())
			if err != nil {
				return nil, err
			}
			keys := []any{}
			for _, f := range r.ToDict() {
				keys = append(keys, f.Key)
			}
			return map[string]any{
				"summary": r.Summary(), "to_dict": raw, "keys": keys,
			}, nil
		}})
	}
	base := Report{RunID: "hb-1", CheckedAt: "2026-08-26T00:00:00+00:00",
		HealthStatus: "healthy", Checks: pyval.Obj{{Key: "a", Val: "ok"}}}
	with := func(f func(*Report)) Report { r := base; f(&r); return r }

	report("R1 an empty stuck list renders the STRING none", base, nil)
	report("R2 a non-empty stuck list renders Python's repr",
		with(func(r *Report) { r.StuckProjects = []string{"alpha", "beta"} }), nil)
	report("R3 one stuck project still renders as a list repr",
		with(func(r *Report) { r.StuckProjects = []string{"alpha"} }), nil)
	report("R4 a stuck name with a quote flips repr to double quotes",
		with(func(r *Report) { r.StuckProjects = []string{"it's"} }), nil)
	report("R5 a stuck name with a backslash",
		with(func(r *Report) { r.StuckProjects = []string{`a\b`} }), nil)
	report("R6 a non-ascii stuck name stays literal in repr",
		with(func(r *Report) { r.StuckProjects = []string{"каф"} }), nil)
	report("R7 recovery actions render one line each",
		with(func(r *Report) {
			r.RecoveryActions = []RecoveryAction{
				{1, "disk_space", "clean up", "suggested", ""},
				{2, "proj", "restart", "escalated", "d"},
			}
		}), nil)
	report("R8 telegram_sent renders True, not true",
		with(func(r *Report) { r.TelegramSent = true; r.ElapsedMS = 1234 }), nil)
	report("R9 quality_summary rides to_dict but NOT summary",
		with(func(r *Report) { r.QualitySummary = "3 frictions" }), nil)
	report("R10 checks with several keys keep their order",
		with(func(r *Report) {
			r.Checks = pyval.Obj{{Key: "z", Val: "ok"}, {Key: "a", Val: "fail"},
				{Key: "m", Val: "warn"}}
		}), nil)
	// The JSON row is what both runtimes append to one ledger, so the
	// escaping rules are part of the comparison, not decoration.
	report("R11 a non-ascii check value survives json.dumps' \\u escaping",
		with(func(r *Report) {
			r.Checks = pyval.Obj{{Key: "ключ", Val: "fail: ошибка"}}
			r.QualitySummary = "3 → 1"
		}), nil)
	report("R12 an empty report still writes all nine keys",
		Report{}, nil)

	// --- _tier3_escalate ---------------------------------------------------
	esc := func(name string, r Report, extra map[string]any) {
		spec := map[string]any{
			"kind": "escalate", "health_status": r.HealthStatus,
			"checks": objPairs(r.Checks), "stuck_projects": strList(r.StuckProjects),
			"actions": actionSpecs(r.RecoveryActions),
		}
		for k, v := range extra {
			spec[k] = v
		}
		wantReturn := true
		if v, ok := extra["returns"].(bool); ok {
			wantReturn = v
		}
		raises, _ := extra["raises"].(string)
		cs = append(cs, hbCase{name: name, spec: spec, run: func() (any, error) {
			var msg any
			called := false
			send := func(m string) (bool, error) {
				msg, called = m, true
				if raises != "" {
					return false, errors.New(raises)
				}
				return wantReturn, nil
			}
			got, err := Tier3Escalate(r, send, func(string) {})
			if err != nil {
				return nil, err
			}
			return map[string]any{
				"returned": got, "message": msg, "called": called,
			}, nil
		}})
	}
	eb := Report{RunID: "r", CheckedAt: "t", HealthStatus: "healthy",
		Checks: pyval.Obj{}}
	ewith := func(f func(*Report)) Report { r := eb; f(&r); return r }

	esc("E1 healthy with nothing stuck does not send", eb, nil)
	esc("E2 healthy WITH a stuck project DOES send",
		ewith(func(r *Report) { r.StuckProjects = []string{"p"} }), nil)
	esc("E3 degraded with nothing stuck sends",
		ewith(func(r *Report) { r.HealthStatus = "degraded" }), nil)
	esc("E4 critical sends and upper-cases the status",
		ewith(func(r *Report) { r.HealthStatus = "critical" }), nil)
	esc("E5 only tier-2 SUGGESTED actions are listed",
		ewith(func(r *Report) {
			r.HealthStatus = "critical"
			r.RecoveryActions = []RecoveryAction{
				{1, "a", "one", "suggested", ""},
				{2, "b", "two", "escalated", ""},
				{2, "c", "three", "suggested", ""},
			}
		}), nil)
	esc("E6 at most three tier-2 suggestions",
		ewith(func(r *Report) {
			r.HealthStatus = "critical"
			for i := 0; i < 5; i++ {
				r.RecoveryActions = append(r.RecoveryActions, RecoveryAction{
					2, fmt.Sprintf("t%d", i), fmt.Sprintf("a%d", i), "suggested", ""})
			}
		}), nil)
	esc("E7 only checks starting with fail are listed, in check order",
		ewith(func(r *Report) {
			r.HealthStatus = "degraded"
			r.Checks = pyval.Obj{{Key: "z", Val: "fail: z"}, {Key: "a", Val: "ok"},
				{Key: "m", Val: "warn: m"}, {Key: "b", Val: "failed"}}
		}), nil)
	esc("E8 no failed checks means no Failed checks line",
		ewith(func(r *Report) {
			r.HealthStatus = "degraded"
			r.Checks = pyval.Obj{{Key: "a", Val: "ok"}}
		}), nil)
	esc("E9 a false return from the notifier is passed through",
		ewith(func(r *Report) { r.HealthStatus = "critical" }),
		map[string]any{"returns": false})
	esc("E10 an unknown status with a stuck project still sends, upper-cased",
		ewith(func(r *Report) {
			r.HealthStatus = "weird"
			r.StuckProjects = []string{"p"}
		}), nil)
	// E10's pair. The gate is a MEMBERSHIP test against two names, not a
	// test against "healthy", so an unrecognised status with nothing stuck
	// is silent. Without this fixture, `status == "healthy"` passes every
	// other escalation case (battery M34).
	esc("E10b an unknown status with nothing stuck is SILENT",
		ewith(func(r *Report) { r.HealthStatus = "weird" }), nil)
	esc("E11 several stuck projects join with a comma-space",
		ewith(func(r *Report) { r.StuckProjects = []string{"a", "b", "c"} }), nil)
	// A notifier that RAISES becomes False — and the message was still
	// built and passed, so `called` stays true.
	esc("E12 a raising notifier is swallowed to False",
		ewith(func(r *Report) { r.HealthStatus = "critical" }),
		map[string]any{"raises": "boom"})
	// ...but a non-string check detail is NOT swallowed: that scan sits
	// above the try. This fixture is the one that tells the two apart.
	esc("E13 an int check detail raises rather than returning False",
		ewith(func(r *Report) {
			r.HealthStatus = "critical"
			r.Checks = pyval.Obj{{Key: "a", Val: 5}}
		}), nil)
	// A tier-2 suggestion is listed even when the gate that opened the
	// message was the stuck list rather than the status.
	esc("E14 tier-2 suggestions ride a stuck-project-only escalation",
		ewith(func(r *Report) {
			r.StuckProjects = []string{"p"}
			r.RecoveryActions = []RecoveryAction{{2, "p", "restart it", "suggested", ""}}
		}), nil)

	// --- the cadence resolvers --------------------------------------------
	// int() accepts far more than an int, and the two resolvers agree at
	// all of it. The Arabic-Indic row was a PINNED DIVERGENCE here until
	// 2026-08-27, when pyval's int lane grew Unicode decimal digits; the
	// pin fired on the day of the fix and this is where its fixture
	// landed. The U+001F row is the other half of that fix: str.strip()
	// removes it and int() does not.
	vals := []any{nil, 0, 1, 5, -3, true, false, 2.9, -2.9, "7", "  7  ",
		"abc", "", []any{}, map[string]any{}, "0x10", "1_0", "+4", "9.5",
		"  -8  ", " 1_0 ", "\u0661\u0667", "\uff11\uff17", "\u001f17",
		"\u00a017"}
	for _, v := range vals {
		v := v
		cs = append(cs, hbCase{
			name: fmt.Sprintf("S %#v", v),
			spec: map[string]any{"kind": "shadow_every", "v": v},
			run: func() (any, error) {
				return float64(ResolveShadowEvery(pyArg(v), nil)), nil
			},
		})
		cs = append(cs, hbCase{
			name: fmt.Sprintf("B %#v", v),
			spec: map[string]any{"kind": "backlog_every", "v": v},
			run: func() (any, error) {
				return float64(ResolveBacklogEvery(pyArg(v), nil)), nil
			},
		})
	}
	return cs
}

// pyArg maps a fixture value to what the Go side would actually receive.
// A JSON round-trip is what happens on the Python side, so an empty list
// and an empty map must reach pyval.Int as values it rejects the way
// CPython's int() rejects them, not as Go nil — which would mean None and
// take the config branch.
func pyArg(v any) any {
	switch t := v.(type) {
	case nil:
		return nil
	case []any:
		return pyval.List{}
	case map[string]any:
		return pyval.Obj{}
	default:
		return t
	}
}

func objPairs(o pyval.Obj) []pair {
	out := []pair{}
	for _, f := range o {
		out = append(out, pair{f.Key, f.Val})
	}
	return out
}

func strList(ss []string) []string {
	if ss == nil {
		return []string{}
	}
	return ss
}

func actionSpecs(as []RecoveryAction) []map[string]any {
	out := []map[string]any{}
	for _, a := range as {
		out = append(out, map[string]any{"tier": a.Tier, "target": a.Target,
			"action": a.Action, "outcome": a.Outcome, "detail": a.Detail})
	}
	return out
}

// TestHeartbeatMatchesCPython is the chunk's differential.
func TestHeartbeatMatchesCPython(t *testing.T) {
	cases := hbCases()
	specs := make([]any, len(cases))
	for i, c := range cases {
		specs[i] = c.spec
	}
	probe := pyprobe.Probe{Marker: "heartbeat.py", Workspace: t.TempDir()}
	var got []map[string]json.RawMessage
	probe.RunJSON(t, hbPySrc, &got, pyprobe.Arg(t, specs))
	if len(got) != len(cases) {
		t.Fatalf("the probe answered %d cases for %d fixtures", len(got), len(cases))
	}

	raised, answered := 0, 0
	for i, c := range cases {
		py := got[i]
		goVal, goErr := c.run()
		if rawErr, isErr := py["err"]; isErr {
			raised++
			var want string
			if err := json.Unmarshal(rawErr, &want); err != nil {
				t.Fatal(err)
			}
			if goErr == nil {
				t.Errorf("%s: CPython raised %s; Go answered %#v", c.name, want, goVal)
				continue
			}
			if got := pyErrText(goErr); got != want {
				t.Errorf("%s:\n  CPython raised: %s\n  Go raised:      %s",
					c.name, want, got)
			}
			continue
		}
		answered++
		if goErr != nil {
			t.Errorf("%s: Go raised %s; CPython answered %s",
				c.name, pyErrText(goErr), py["ok"])
			continue
		}
		wantJSON := string(py["ok"])
		gotJSON, err := json.Marshal(goVal)
		if err != nil {
			t.Fatal(err)
		}
		if !sameJSON(t, string(gotJSON), wantJSON) {
			t.Errorf("%s:\n  CPython: %s\n  Go:      %s", c.name, wantJSON, gotJSON)
		}
	}

	// Anti-vacuity: a differential where one lane is empty passes against a
	// port that only implements the other, and passes just as green after a
	// refactor quietly removes every raising fixture. P7's baseline-green
	// gate does not catch that — a baseline can be green AND empty.
	if raised < 5 || answered < 40 {
		t.Fatalf("the fixture set stopped exercising both lanes: %d raises, "+
			"%d answers", raised, answered)
	}
}

// sameJSON compares two JSON documents structurally, so that a Go int
// marshalled as 5 and a Python one decoded as 5.0 are not a false failure —
// while a STRING "5" still is.
func sameJSON(t *testing.T, a, b string) bool {
	t.Helper()
	var x, y any
	if err := json.Unmarshal([]byte(a), &x); err != nil {
		t.Fatalf("go side is not JSON: %v (%s)", err, a)
	}
	if err := json.Unmarshal([]byte(b), &y); err != nil {
		t.Fatalf("python side is not JSON: %v (%s)", err, b)
	}
	return fmt.Sprintf("%#v", x) == fmt.Sprintf("%#v", y)
}

// TestResolverConfigLaneIsNotTheExplicitLane covers the branch every
// differential fixture skips: `explicit is None`, so the value comes from
// config.
//
// The differential cannot reach it — pyprobe points MARO_USER_DIR at a temp
// dir on purpose, so CPython's `config.get` always answers the default and
// every `None` fixture tests the same one line. The battery walked straight
// through a version that fell to the FLOOR instead of the DEFAULT on a
// config error (M64), which is invisible for shadow (floor and default are
// both 0) and wrong by four for backlog.
func TestResolverConfigLaneIsNotTheExplicitLane(t *testing.T) {
	boom := func() (any, error) { return nil, errors.New("unreadable config") }
	if got := ResolveBacklogEvery(nil, boom); got != DefaultBacklogEvery {
		t.Errorf("a config read that fails must land on the DEFAULT (%d), "+
			"not the floor; got %d", DefaultBacklogEvery, got)
	}
	junk := func() (any, error) { return "abc", nil }
	if got := ResolveBacklogEvery(nil, junk); got != DefaultBacklogEvery {
		t.Errorf("a config value int() refuses lands on the default; got %d", got)
	}
	// ...and a usable config value is read, floored, and beats the default.
	if got := ResolveBacklogEvery(nil, func() (any, error) { return -3, nil }); got != 1 {
		t.Errorf("a config -3 clamps to the backlog floor 1; got %d", got)
	}
	if got := ResolveShadowEvery(nil, func() (any, error) { return "12", nil }); got != 12 {
		t.Errorf("a config string is read the way int() reads it; got %d", got)
	}
	// A nil thunk is "no config at all", which is the default and must not
	// panic — this is the shape every differential fixture passes.
	if got := ResolveShadowEvery(nil, nil); got != DefaultShadowEvery {
		t.Errorf("no config: got %d, want %d", got, DefaultShadowEvery)
	}
	// The thunk must not be CALLED when an explicit value was given: the
	// Python read sits inside the else branch, and a config backend that
	// fails must not be consulted on a path that never reads it.
	called := false
	got := ResolveBacklogEvery(9, func() (any, error) { called = true; return 1, nil })
	if called {
		t.Error("the config thunk ran even though an explicit value was given")
	}
	if got != 9 {
		t.Errorf("explicit 9 resolved to %d", got)
	}
}

// TestCooldownIsPerProjectAndNeverDiagnosedIsDue covers the tier-2 gate,
// which has no CPython fixture because its input is a monotonic clock.
// The BEHAVIOUR under test is the one the Python comment warns about: a
// project never diagnosed must be due even when the clock reads zero.
func TestCooldownIsPerProjectAndNeverDiagnosedIsDue(t *testing.T) {
	// The LITERAL, not the constant. Every assertion below is written in
	// terms of DiagnosisCooldown, so halving the constant changes nothing
	// they can see — the battery (M56) walked straight through. A test
	// whose expected value is spelled with the thing under test is not a
	// test of that thing.
	if DiagnosisCooldown != 30*time.Minute {
		t.Fatalf("DiagnosisCooldown is %v, want 30m — Python's "+
			"_DIAGNOSIS_COOLDOWN_SECS is 1800", DiagnosisCooldown)
	}
	var now time.Duration
	c := NewCooldown()
	c.Now = func() time.Duration { return now }

	if !c.Due("alpha") {
		t.Fatal("a never-diagnosed project must be due at clock zero — the " +
			"0.0-sentinel bug the Python comment names")
	}
	c.MarkRan("alpha")
	if c.Due("alpha") {
		t.Fatal("alpha is on cooldown immediately after MarkRan")
	}
	if !c.Due("beta") {
		t.Fatal("the cooldown is per project; beta was never diagnosed")
	}
	now = DiagnosisCooldown - 1
	if c.Due("alpha") {
		t.Fatalf("alpha is due one nanosecond early")
	}
	now = DiagnosisCooldown
	if !c.Due("alpha") {
		t.Fatal("the comparison is >=, so alpha is due exactly at the cooldown")
	}
}

// TestLogWritesOneJsonlRowAtTheDocumentedPath covers _log_heartbeat's two
// observable effects — WHERE the row lands and that it is one line — which
// the report differential (which only compares the rendered row) does not.
func TestLogWritesOneJsonlRowAtTheDocumentedPath(t *testing.T) {
	ws := t.TempDir()
	r := Report{RunID: "hb-1", HealthStatus: "degraded",
		Checks:        pyval.Obj{{Key: "disk_space", Val: "warn: 92%"}},
		StuckProjects: []string{"alpha"}}
	path, err := Log(ws, r)
	if err != nil {
		t.Fatal(err)
	}
	// The LITERAL path, not LogPath(ws) — an assertion spelled with the
	// function under test cannot fail when that function changes, and the
	// battery renamed the file and moved it out of memory/ without either
	// mutant being caught (M58, M59).
	if want := filepath.Join(ws, "memory", "heartbeat-log.jsonl"); path != want {
		t.Fatalf("Log wrote to %q, want %q", path, want)
	}
	if _, err := Log(ws, r); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n")
	if len(lines) != 2 {
		t.Fatalf("two appends produced %d lines:\n%s", len(lines), raw)
	}
	// The separators are json.dumps' DEFAULTS — ", " and ": ", not the
	// compact ones. A reader splitting on lines does not care, but a
	// byte-for-byte ledger comparison between the two runtimes does.
	if !strings.Contains(lines[0], `"run_id": "hb-1"`) {
		t.Fatalf("the row is not in json.dumps' default separators: %s", lines[0])
	}
	// One line per append, which is what makes it JSONL — an indent-2
	// renderer here writes a valid JSON object and an unreadable ledger.
	if strings.Contains(lines[0], "\n  ") {
		t.Fatalf("the row is indented, so the ledger is not JSONL: %s", lines[0])
	}
}

// TestLogReportsNoPathWhenTheAppendFails covers _log_heartbeat's failure
// half: Python's `except Exception: return None`, so the caller prints
// nothing rather than a path that holds nothing. The battery walked through
// a version that returned the path anyway (M61), because no test ever made
// the write fail.
func TestLogReportsNoPathWhenTheAppendFails(t *testing.T) {
	ws := t.TempDir()
	// memory/ as a FILE, so creating the log inside it cannot succeed.
	if err := os.WriteFile(filepath.Join(ws, "memory"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	path, err := Log(ws, Report{RunID: "hb-1"})
	if err == nil {
		t.Fatal("appending under a regular file must fail")
	}
	if path != "" {
		t.Fatalf("Log reported %q for a write that failed — Python returns None "+
			"and the caller prints it", path)
	}
}
