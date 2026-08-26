package syshealth

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// syPySrc drives the real system_health.py. It is the same probe that
// captured this chunk's ground truth BEFORE any Go existed
// (scratchpad/sy_probe.py), carried here verbatim rather than paraphrased —
// the ground-truth pass and the differential must not be able to drift
// apart (L49).
//
// Three of its patches are the whole reason the cycle is testable, and each
// answers a seam the port had to reproduce:
//
//   - DECLARED_PROCESSES is REPLACED with scripted probes. The seven real
//     ones each read a different live store; with them in place there is no
//     way to ask "what does a SILENT do to the snapshot" at all. The Go side
//     takes them as a parameter for the same reason.
//   - sh.datetime is frozen. `datetime.now(timezone.utc).isoformat()` is
//     called once per declaration and once more for updated_at, so a fixture
//     that let the real clock through would compare four moving strings.
//     The Go side takes a clock FUNCTION, not a string, so the shape (how
//     many times it is read) survives the seam.
//   - _narrate_transition is captured into a list. It is a captains_log
//     write, and what the differential needs to compare is WHICH transitions
//     were decided, not that a log file grew.
//
// config.get is patched only for `health.probes_enabled`, and only on cases
// that ask — an unconditional patch would make the enabled-by-default cases
// test the patch instead of the default.
const syPySrc = `
import datetime as _dtmod
import json, os, pathlib, sys
import system_health as sh

FROZEN = "2026-08-26T12:00:00+00:00"


class _FakeDT:
    @staticmethod
    def now(tz=None):
        return _dtmod.datetime(2026, 8, 26, 12, 0, 0, tzinfo=tz)


def _decl(name, spec):
    """A ProcessDeclaration whose probe replays a scripted answer."""
    def probe(prior, _s=spec):
        if "raise" in _s:
            raise RuntimeError(_s["raise"])
        return (_s["status"], _s["evidence"], _s.get("obs") or {})
    return sh.ProcessDeclaration(
        name=name, description=spec.get("description", "d:" + name),
        expectation=spec.get("expectation", "e:" + name), probe=probe)


def _snap_bytes():
    p = sh._snapshot_path()
    return p.read_text(encoding="utf-8") if p.exists() else None


out = []
for c in json.loads(sys.argv[1]):
    try:
        k = c["kind"]
        if k == "cycle":
            path = sh._snapshot_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if c.get("prior_raw") is not None:
                path.write_text(c["prior_raw"], encoding="utf-8")
            elif c.get("prior") is not None:
                path.write_text(json.dumps(c["prior"]), encoding="utf-8")
            elif path.exists():
                path.unlink()

            told = []
            real_narrate, real_decls, real_dt = (
                sh._narrate_transition, sh.DECLARED_PROCESSES, sh.datetime)
            sh._narrate_transition = (
                lambda d, s, e: told.append([d.name, s, e]))
            sh.DECLARED_PROCESSES = [
                _decl(n, s) for n, s in c["probes"]]
            sh.datetime = _FakeDT
            import config as _cfg
            real_get = _cfg.get
            if "enabled" in c:
                _cfg.get = lambda key, default=None: (
                    c["enabled"] if key == "health.probes_enabled"
                    else real_get(key, default))
            try:
                summary = sh.run_health_probes()
            finally:
                sh._narrate_transition, sh.DECLARED_PROCESSES, sh.datetime = (
                    real_narrate, real_decls, real_dt)
                _cfg.get = real_get
            out.append({"ok": {"summary": summary, "told": told,
                               "file": _snap_bytes()}})
        elif k == "render":
            out.append({"ok": sh.render_snapshot(c["snap"])})
        elif k == "history":
            out.append({"ok": sh._history_of(c["prior"])})
        elif k == "load":
            path = sh._snapshot_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if c["raw"] is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_text(c["raw"], encoding="utf-8")
            out.append({"ok": sh.load_snapshot()})
        elif k == "path":
            # Where the module RESOLVES its store, relative to the
            # workspace. Nothing else in this probe can see it: every other
            # case both writes and reads through _snapshot_path, so a port
            # that agreed on the shape and disagreed on the location would
            # pass all of them.
            out.append({"ok": os.path.relpath(
                str(sh._snapshot_path()), os.environ["MARO_WORKSPACE"])})
        elif k == "dumps1":
            out.append({"ok": json.dumps(c["v"], indent=1, sort_keys=True)})
        elif k == "consts":
            out.append({"ok": {"OK": sh.OK, "SILENT": sh.SILENT,
                               "UNKNOWN": sh.UNKNOWN,
                               "HISTORY_KEEP": sh.HISTORY_KEEP,
                               "STREAK_FOR_SILENT": sh.STREAK_FOR_SILENT,
                               "CANDIDATE_STARVATION_HOURS":
                                   sh.CANDIDATE_STARVATION_HOURS,
                               "VARIANT_STALE_DAYS": sh.VARIANT_STALE_DAYS}})
        else:
            out.append({"err": "unknown kind " + k})
    except Exception as e:
        out.append({"err": type(e).__name__ + ": " + str(e)})
print(json.dumps(out))
`

const syFrozen = "2026-08-26T12:00:00+00:00"

// syCase is one fixture. `spec` goes to CPython as-is; `run` produces the
// Go answer, which is compared against the probe's `ok` — or, when the
// Python RAISED, against `err`. Unlike the sheriff differential, a raise
// here is a real behaviour: render_snapshot has three raising branches and
// the port reproduces all three.
type syCase struct {
	name string
	spec any
	run  func(t *testing.T, ws string) (any, error)
}

// syProbes turns the fixture's [[name, spec], ...] into Declarations whose
// probes replay the scripted answer — the Go half of the probe's `_decl`.
func syProbes(t *testing.T, raw string) []Declaration {
	t.Helper()
	var pairs []json.RawMessage
	if err := json.Unmarshal([]byte(raw), &pairs); err != nil {
		t.Fatal(err)
	}
	out := make([]Declaration, 0, len(pairs))
	for _, p := range pairs {
		var pair []json.RawMessage
		if err := json.Unmarshal(p, &pair); err != nil {
			t.Fatal(err)
		}
		var name string
		if err := json.Unmarshal(pair[0], &name); err != nil {
			t.Fatal(err)
		}
		spec := syObj(t, string(pair[1]))
		desc := "d:" + name
		if v, ok := spec.Get("description"); ok {
			desc = pyval.Str(v)
		}
		exp := "e:" + name
		if v, ok := spec.Get("expectation"); ok {
			exp = pyval.Str(v)
		}
		s := spec
		out = append(out, Declaration{
			Name: name, Description: desc, Expectation: exp,
			Probe: func(prior pyval.Obj) (string, string, pyval.Obj, error) {
				if v, ok := s.Get("raise"); ok {
					return "", "", nil, fmt.Errorf("%s", pyval.Str(v))
				}
				st, _ := s.Get("status")
				ev, _ := s.Get("evidence")
				var obs pyval.Obj
				if o, ok := s.Get("obs"); ok {
					if oo, isObj := o.(pyval.Obj); isObj {
						obs = oo
					}
				}
				if obs == nil {
					obs = pyval.Obj{}
				}
				return pyval.Str(st), pyval.Str(ev), obs, nil
			},
		})
	}
	return out
}

// syObj parses a JSON object literal into an ordered Obj. Fixtures are
// written as JSON TEXT rather than as Go values so that both runtimes are
// handed the same bytes: a Go map literal would be marshalled sorted before
// CPython ever saw it, which would erase every insertion-order question
// this chunk asks (`{**obs, "at": now}`, `dict(prior)`).
func syObj(t *testing.T, raw string) pyval.Obj {
	t.Helper()
	v, err := pyval.LoadsOrdered(raw)
	if err != nil {
		t.Fatalf("fixture %s is not JSON: %v", raw, err)
	}
	o, ok := v.(pyval.Obj)
	if !ok {
		t.Fatalf("fixture %s is not an object", raw)
	}
	return o
}

// syGo converts pyval's ordered shapes into what encoding/json can compare.
// The comparison is semantic (sameJSON), so dropping order here is safe —
// and everywhere order IS observable it has already been baked into a
// STRING by the code under test (the snapshot file, the rendered report).
//
// The nil checks are not defensive noise, they are the whole reason this
// helper is dangerous: a NIL slice marshals to `null` and an empty one to
// `[]`, and CPython's `summary["silent"]` is always a list. Converting nil
// into `make([]any, 0)` would erase that difference and let `var sil
// pyval.List` pass the differential — the test's own renderer becoming the
// guard that fails, which is exactly how sheriff's M76 got through. Battery
// M1 and M17 are the pins.
func syGo(v any) any {
	switch t := v.(type) {
	case pyval.Obj:
		if t == nil {
			return nil
		}
		m := map[string]any{}
		for _, f := range t {
			m[f.Key] = syGo(f.Val)
		}
		return m
	case pyval.List:
		if t == nil {
			return nil
		}
		out := make([]any, len(t))
		for i, e := range t {
			out[i] = syGo(e)
		}
		return out
	}
	return v
}

func syWriteSnap(t *testing.T, ws, raw string) {
	t.Helper()
	path := SnapshotPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(raw), 0o644); err != nil {
		t.Fatal(err)
	}
}

// syCycle is the Go half of `run_health_probes` for one fixture: seed the
// file, load, run, write. The write is the caller's job in the port (see
// the package doc), so the differential performs it here — otherwise the
// `file` half of every cycle fixture would compare against nothing.
func syCycle(t *testing.T, ws, priorRaw, probes string, enabled any) (any, error) {
	t.Helper()
	path := SnapshotPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if priorRaw != "" {
		if err := os.WriteFile(path, []byte(priorRaw), 0o644); err != nil {
			t.Fatal(err)
		}
	} else {
		os.Remove(path)
	}
	snap := LoadSnapshot(ws)
	next, told, summary := RunCycle(snap, syProbes(t, probes), enabled,
		func() string { return syFrozen })
	if next != nil {
		if err := WriteSnapshot(ws, next); err != nil {
			t.Fatal(err)
		}
	}
	toldOut := []any{}
	for _, n := range told {
		toldOut = append(toldOut, []any{n.Decl.Name, n.Status, n.Evidence})
	}
	var file any
	if b, err := os.ReadFile(path); err == nil {
		file = string(b)
	}
	return map[string]any{
		"summary": syGo(summary.ToDict()),
		"told":    toldOut,
		"file":    file,
	}, nil
}

func syCases() []syCase {
	var cs []syCase
	add := func(name string, spec any, run func(*testing.T, string) (any, error)) {
		cs = append(cs, syCase{name, spec, run})
	}

	add("Z1 the constants", map[string]any{"kind": "consts"},
		func(t *testing.T, ws string) (any, error) {
			return map[string]any{
				"OK": OK, "SILENT": Silent, "UNKNOWN": Unknown,
				"HISTORY_KEEP": HistoryKeep, "STREAK_FOR_SILENT": StreakForSilent,
				"CANDIDATE_STARVATION_HOURS": CandidateStarvationHours,
				"VARIANT_STALE_DAYS":         VariantStaleDays,
			}, nil
		})

	// Battery M7 ("the snapshot file is named differently") survived a whole
	// round because every other fixture both WRITES and READS through
	// SnapshotPath: rename the file consistently and nothing notices. This
	// is the only case that looks at the location itself.
	add("Z2 where the snapshot lives", map[string]any{"kind": "path"},
		func(t *testing.T, ws string) (any, error) {
			rel, err := filepath.Rel(ws, SnapshotPath(ws))
			return rel, err
		})

	// --- load_snapshot -----------------------------------------------------
	// The five collapse-to-{} lanes plus a real snapshot. `nil` raw means the
	// file is DELETED, which is a different lane from an empty file.
	for _, tc := range []struct{ name, raw string }{
		{"S1 no file at all", "\x00"},
		{"S2 an empty file", ""},
		{"S3 not json", "{nope"},
		{"S4 a json LIST is not a dict", "[1, 2]"},
		{"S5 a json string is not a dict", `"hello"`},
		{"S6 json null is not a dict", "null"},
		{"S7 a real snapshot", `{"cycle": 3, "processes": {}}`},
	} {
		raw := tc.raw
		spec := map[string]any{"kind": "load", "raw": raw}
		if raw == "\x00" {
			spec["raw"] = nil
		}
		add(tc.name, spec, func(t *testing.T, ws string) (any, error) {
			if raw == "\x00" {
				os.Remove(SnapshotPath(ws))
			} else {
				syWriteSnap(t, ws, raw)
			}
			return syGo(LoadSnapshot(ws)), nil
		})
	}

	// --- _history_of -------------------------------------------------------
	for _, tc := range []struct{ name, prior string }{
		{"H1 no history key", `{}`},
		{"H2 history is not a list", `{"history": {"a": 1}}`},
		{"H3 history holds non-dicts", `{"history": [1, "x", {"a": 1}, null]}`},
		{"H4 history is empty", `{"history": []}`},
		{"H5 history is a string", `{"history": "abc"}`},
	} {
		prior := tc.prior
		add(tc.name, map[string]any{"kind": "history",
			"prior": json.RawMessage(prior)},
			func(t *testing.T, ws string) (any, error) {
				return syGo(HistoryOf(syObj(t, prior))), nil
			})
	}

	// --- json.dumps(indent=1, sort_keys=True) ------------------------------
	for _, tc := range []struct{ name, v string }{
		{"J1 flat keys sort", `{"b": 1, "a": 2, "C": 3}`},
		{"J2 nested dicts sort too", `{"z": {"b": 1, "a": 2}, "a": [3, {"y": 1, "x": 2}]}`},
		{"J3 digits and underscores", `{"a10": 1, "a2": 2, "a_": 3, "a": 4}`},
		{"J4 an empty key", `{"": 1, "a": 2}`},
		{"J5 non-ascii keys sort by code point and escape", `{"é": 1, "z": 2, "à": 3}`},
		{"J6 empty containers", `{"a": {}, "b": [], "c": null}`},
		{"J7 a float and a bool", `{"f": 2.5, "t": true, "n": null}`},
		{"J8 the empty object", `{}`},
	} {
		v := tc.v
		add(tc.name, map[string]any{"kind": "dumps1", "v": json.RawMessage(v)},
			func(t *testing.T, ws string) (any, error) {
				return pyval.DumpsIndentNSorted(syObj(t, v), 1)
			})
	}

	// --- render_snapshot ---------------------------------------------------
	const pOK = `{"status": "OK", "description": "d1", "expectation": "e1", "evidence": "ev1"}`
	const pSilent = `{"status": "SILENT", "description": "d2", "expectation": "e2",` +
		` "evidence": "ev2", "last_transition": {"from": "UNKNOWN", "to": "SILENT",` +
		` "at": "2026-08-26T12:00:00+00:00"}}`
	const pUnknown = `{"status": "UNKNOWN", "description": "d3", "expectation": "e3", "evidence": "ev3"}`
	for _, tc := range []struct{ name, snap string }{
		{"R1 no processes key", `{}`},
		{"R2 an EMPTY processes dict is also 'no snapshot yet'", `{"processes": {}}`},
		{"R3 the status ordering is SILENT, UNKNOWN, OK, then by name",
			`{"updated_at": "2026-08-26T12:00:00+00:00", "cycle": 4, "processes": {"aaa": ` +
				pOK + `, "bbb": ` + pSilent + `, "ccc": ` + pUnknown + `}}`},
		{"R4 ties inside one status break by name",
			`{"updated_at": "t", "cycle": 1, "processes": {"zz": ` + pOK +
				`, "aa": ` + pOK + `, "mm": ` + pOK + `}}`},
		{"R5 an unknown status sorts last",
			`{"updated_at": "t", "cycle": 1, "processes": {"a": {"status": "WEIRD"}, "b": ` + pOK + `}}`},
		{"R6 a NULL updated_at renders 'None', not '?'",
			`{"updated_at": null, "cycle": null, "processes": {"a": ` + pOK + `}}`},
		{"R7 a missing updated_at renders '?'", `{"processes": {"a": ` + pOK + `}}`},
		{"R8 a process with no fields at all",
			`{"updated_at": "t", "cycle": 1, "processes": {"a": {}}}`},
		{"R9 last_transition with missing parts",
			`{"updated_at": "t", "cycle": 1, "processes": {"a": {"status": "OK", "last_transition": {}}}}`},
		{"R10 last_transition that is not a dict is skipped",
			`{"updated_at": "t", "cycle": 1, "processes": {"a": {"status": "OK", "last_transition": "nope"}}}`},
		{"R11 a process entry that is not a dict",
			`{"updated_at": "t", "cycle": 1, "processes": {"a": "nope"}}`},
		{"R12 processes is a LIST",
			`{"updated_at": "t", "cycle": 1, "processes": [1, 2]}`},
		// A dict status is unhashable for the same reason a list is, and the
		// port's TypeError arm names two types — so it needs two fixtures or
		// half of it is unproven.
		{"R16 a DICT status is unhashable too",
			`{"updated_at": "t", "cycle": 1, "processes": {"a": {"status": {}}}}`},
		// The sort key reads `.get("status")` with NO default, so a MISSING
		// status ranks 3 (below OK) while the DISPLAY renders "?". Two
		// processes, so the rank is observable at all: "a" sorts after "b".
		{"R17 a MISSING status does not sort with the OK group",
			`{"updated_at": "t", "cycle": 1, "processes": {"a": {"description": "no status here"},` +
				` "b": {"status": "OK", "description": "d"}}}`},
		// R11's entry is a STRING, so a port that hard-coded 'str' in the
		// AttributeError would match it. This one is a list.
		{"R15 a LIST process entry names its own type in the error",
			`{"updated_at": "t", "cycle": 1, "processes": {"a": [1]}}`},
		// The third raising branch, and the least obvious: the sort key is
		// `order.get(status, 3)`, a DICT lookup, so an unhashable status is
		// a TypeError rather than a miss that falls to rank 3. Without this
		// fixture the port's TypeError arm is a claim nobody checked.
		{"R14 an unhashable status is a TypeError, not a miss",
			`{"updated_at": "t", "cycle": 1, "processes": {"a": {"status": ["OK"]}}}`},
		{"R13 non-ascii description",
			`{"updated_at": "t", "cycle": 1, "processes": {"a": {"status": "OK",` +
				` "description": "café → naïve", "expectation": "e", "evidence": "ev"}}}`},
	} {
		snap := tc.snap
		add(tc.name, map[string]any{"kind": "render", "snap": json.RawMessage(snap)},
			func(t *testing.T, ws string) (any, error) {
				return RenderSnapshot(syObj(t, snap))
			})
	}

	// --- the cycle state machine -------------------------------------------
	const okp = `{"status": "OK", "evidence": "fine"}`
	const sip = `{"status": "SILENT", "evidence": "quiet"}`
	const unp = `{"status": "UNKNOWN", "evidence": "dunno"}`
	type cy struct {
		name    string
		probes  string
		prior   string // JSON object text, or "" for no prior key at all
		rawFile string // raw bytes to seed the file with, wins over prior
		enabled any    // nil means the fixture does not patch config
	}
	// The ring-buffer fixture needs a prior history of exactly HISTORY_KEEP,
	// so it is built rather than spelled: eight entries n=0..7, plus the new
	// n=9, keeps 1..7 and 9. A hand-written eight would be a constant that
	// stops meaning "HISTORY_KEEP" the moment the constant moves.
	ring := make([]string, HistoryKeep)
	for i := range ring {
		ring[i] = fmt.Sprintf(`{"n": %d}`, i)
	}
	ringPrior := `{"processes": {"p1": {"history": [` + strings.Join(ring, ", ") + `]}}}`

	for _, tc := range []cy{
		{name: "C1 a first cycle on an empty box", probes: `[["p1", ` + okp + `]]`},
		{name: "C2 a first observation of SILENT narrates", probes: `[["p1", ` + sip + `]]`},
		{name: "C3 SILENT held does NOT re-narrate", probes: `[["p1", ` + sip + `]]`,
			prior: `{"processes": {"p1": {"status": "SILENT", "narrated": "silent"}}}`},
		{name: "C4 SILENT -> OK narrates a recovery", probes: `[["p1", ` + okp + `]]`,
			prior: `{"processes": {"p1": {"status": "SILENT", "narrated": "silent"}}}`},
		{name: "C5 SILENT -> UNKNOWN -> OK still narrates the recovery", probes: `[["p1", ` + okp + `]]`,
			prior: `{"processes": {"p1": {"status": "UNKNOWN", "narrated": "silent"}}}`},
		{name: "C6 UNKNOWN -> SILENT narrates", probes: `[["p1", ` + sip + `]]`,
			prior: `{"processes": {"p1": {"status": "UNKNOWN"}}}`},
		{name: "C7 OK held never narrates", probes: `[["p1", ` + okp + `]]`,
			prior: `{"processes": {"p1": {"status": "OK", "narrated": "ok"}}}`},
		{name: "C8 an OK that was never told silent does not narrate a recovery",
			probes: `[["p1", ` + okp + `]]`,
			prior:  `{"processes": {"p1": {"status": "OK"}}}`},
		{name: "C9 a probe that RAISES reports UNKNOWN with a clipped message",
			probes: `[["p1", {"raise": "` + strings.Repeat("x", 200) + `"}]]`},
		{name: "C10 a raising probe's message is clipped at 120",
			probes: `[["p1", {"raise": "` + strings.Repeat("abcdefghij", 20) + `"}]]`},
		{name: "C11 an empty observation is falsy and appends no history",
			probes: `[["p1", {"status": "OK", "evidence": "e", "obs": {}}]]`},
		{name: "C12 an observation is stamped with 'at'",
			probes: `[["p1", {"status": "OK", "evidence": "e", "obs": {"n": 1}}]]`},
		{name: "C13 a probe-supplied 'at' is OVERWRITTEN",
			probes: `[["p1", {"status": "OK", "evidence": "e", "obs": {"at": "1999", "n": 1}}]]`},
		{name: "C14 history is a ring buffer of eight",
			probes: `[["p1", {"status": "OK", "evidence": "e", "obs": {"n": 9}}]]`,
			prior:  ringPrior},
		{name: "C15 unknown prior keys survive the update", probes: `[["p1", ` + okp + `]]`,
			prior: `{"processes": {"p1": {"status": "OK", "mine": "keep me"}}}`},
		{name: "C16 several processes, mixed statuses",
			probes: `[["p1", ` + okp + `], ["p2", ` + sip + `], ["p3", ` + unp + `]]`},
		{name: "C17 the cycle counter increments", probes: `[["p1", ` + okp + `]]`,
			prior: `{"cycle": 41, "processes": {}}`},
		{name: "C18 a STRING cycle counter is int()ed", probes: `[["p1", ` + okp + `]]`,
			prior: `{"cycle": "41", "processes": {}}`},
		{name: "C19 a FLOAT cycle counter truncates", probes: `[["p1", ` + okp + `]]`,
			prior: `{"cycle": 2.9, "processes": {}}`},
		{name: "C20 a falsy cycle counter restarts at 1", probes: `[["p1", ` + okp + `]]`,
			prior: `{"cycle": null, "processes": {}}`},
		{name: "C21 an EMPTY-STRING cycle counter restarts at 1", probes: `[["p1", ` + okp + `]]`,
			prior: `{"cycle": "", "processes": {}}`},
		{name: "C22 an UNPARSEABLE cycle counter takes the whole cycle down",
			probes: `[["p1", ` + okp + `]]`, prior: `{"cycle": "abc", "processes": {}}`},
		{name: "C23 a processes value that is not a dict is replaced",
			probes: `[["p1", ` + okp + `]]`, prior: `{"processes": [1, 2]}`},
		{name: "C24 a prior entry that is not a dict is treated as empty",
			probes: `[["p1", ` + okp + `]]`, prior: `{"processes": {"p1": "nope"}}`},
		{name: "C25 unrelated top-level keys survive",
			probes: `[["p1", ` + okp + `]]`, prior: `{"mine": 1, "processes": {}}`},
		{name: "C26 a process absent from this cycle's declarations is left alone",
			probes: `[["p1", ` + okp + `]]`,
			prior:  `{"processes": {"gone": {"status": "SILENT", "narrated": "silent"}}}`},
		{name: "C27 probes_enabled=False skips the whole cycle",
			probes: `[["p1", ` + okp + `]]`, enabled: false},
		{name: "C28 probes_enabled=0 is falsy too",
			probes: `[["p1", ` + okp + `]]`, enabled: 0},
		{name: "C29 probes_enabled='' is falsy",
			probes: `[["p1", ` + okp + `]]`, enabled: ""},
		{name: "C30 probes_enabled='no' is a non-empty string, so TRUE",
			probes: `[["p1", ` + okp + `]]`, enabled: "no"},
		{name: "C31 a corrupt snapshot file starts from scratch",
			probes: `[["p1", ` + okp + `]]`, rawFile: "{not json"},
		{name: "C32 an evidence string with a newline",
			probes: `[["p1", {"status": "SILENT", "evidence": "a\nb"}]]`},
		// C34 separates the two spellings of the narration guard. Reading
		// `prev != SILENT` instead of `narrated != "silent"` passes every
		// other cycle fixture and fails only here.
		{name: "C34 a told-silent process that dipped to UNKNOWN and is SILENT again does not re-narrate",
			probes: `[["p1", ` + sip + `]]`,
			prior:  `{"processes": {"p1": {"status": "UNKNOWN", "narrated": "silent"}}}`},
		// Two narrations in one cycle, so the pending list has an ORDER to
		// get wrong. Every other fixture narrates at most once.
		{name: "C35 two silences narrate in declaration order",
			probes: `[["p1", ` + sip + `], ["p2", {"status": "SILENT", "evidence": "also quiet"}]]`},
		// The only fixture where the 200-char error clip does anything: the
		// int() message embeds the repr of the whole offending string.
		{name: "C36 an int() message longer than 200 chars is clipped",
			probes: `[["p1", ` + okp + `]]`,
			prior:  `{"cycle": "` + strings.Repeat("z", 300) + `", "processes": {}}`},
		// transitions is assigned AFTER the write; a port that assigns it
		// before the counter can raise reports 1 here.
		{name: "C37 a cycle that dies still reports zero transitions",
			probes: `[["p1", ` + sip + `]]`, prior: `{"cycle": "abc", "processes": {}}`},
		{name: "C33 non-ascii evidence and description",
			probes: `[["p1", {"status": "SILENT", "evidence": "café", "description": "d é", "expectation": "e é"}]]`},
	} {
		tc := tc
		spec := map[string]any{"kind": "cycle", "probes": json.RawMessage(tc.probes)}
		if tc.rawFile != "" {
			spec["prior_raw"] = tc.rawFile
		} else if tc.prior != "" {
			spec["prior"] = json.RawMessage(tc.prior)
		}
		// The probe reads `"enabled" in c`, so the key must be ABSENT — not
		// null — on the cases that do not patch config.
		enabled := any(true)
		if tc.enabled != nil {
			spec["enabled"] = tc.enabled
			enabled = tc.enabled
		}
		add(tc.name, spec, func(t *testing.T, ws string) (any, error) {
			seed := tc.prior
			if tc.rawFile != "" {
				seed = tc.rawFile
			}
			return syCycle(t, ws, seed, tc.probes, enabled)
		})
	}
	return cs
}

func TestSystemHealthMatchesCPython(t *testing.T) {
	cases := syCases()
	specs := make([]any, len(cases))
	for i, c := range cases {
		specs[i] = c.spec
	}
	probe := pyprobe.Probe{Marker: "system_health.py", Workspace: t.TempDir()}
	var got []map[string]json.RawMessage
	probe.RunJSON(t, syPySrc, &got, pyprobe.Arg(t, specs))
	if len(got) != len(cases) {
		t.Fatalf("the probe answered %d cases for %d fixtures", len(got), len(cases))
	}

	// The Go side gets its OWN workspace. The probe writes a real snapshot
	// file into its own, and a shared one would let the Python pass seed the
	// file the Go pass then reads — a differential comparing one runtime
	// against itself.
	ws := t.TempDir()

	// Anti-vacuity, counted rather than asserted per case: a fixture set that
	// drifts until nothing raises, nothing narrates and nothing goes SILENT
	// still passes every comparison below. See the gate after the loop.
	var raised, narrated, wroteFile int
	for i, c := range cases {
		py := got[i]
		goVal, goErr := c.run(t, ws)
		if rawErr, isErr := py["err"]; isErr {
			raised++
			var want string
			if err := json.Unmarshal(rawErr, &want); err != nil {
				t.Fatal(err)
			}
			if goErr == nil {
				t.Errorf("%s: CPython raised %s, the port returned %#v", c.name, want, goVal)
				continue
			}
			// The probe spells it `type(e).__name__ + ": " + str(e)`, which
			// is exactly PyErr's Class and Msg — and the reason PyErr keeps
			// the class in a FIELD rather than in Error().
			var gotStr string
			if pe, ok := goErr.(*pyval.PyErr); ok {
				gotStr = pe.Class + ": " + pe.Msg
			} else {
				gotStr = goErr.Error()
			}
			if gotStr != want {
				t.Errorf("%s:\n  CPython raised: %s\n  Go raised:      %s", c.name, want, gotStr)
			}
			continue
		}
		if goErr != nil {
			t.Errorf("%s: the port raised %v, CPython answered %s", c.name, goErr, py["ok"])
			continue
		}
		if m, ok := goVal.(map[string]any); ok {
			if sum, ok := m["summary"].(map[string]any); ok {
				if n, ok := sum["transitions"].(int); ok && n > 0 {
					narrated += n
				}
			}
			if _, ok := m["file"].(string); ok {
				wroteFile++
			}
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

	// Three counts, because the three interesting behaviours fail silently in
	// three different ways. `raised` covers render_snapshot's AttributeError
	// branches — the ones a tolerant port would replace with a placeholder.
	// `narrated` covers the edge-trigger: a port that never narrates and a
	// fixture set that never transitions look identical from here.
	// `wroteFile` covers the write itself — C22 and C27 both answer
	// `"file": null`, and a port that wrote nothing ever would match them.
	if raised < 3 {
		t.Errorf("only %d fixtures raised; render_snapshot has three raising "+
			"branches and this differential is meant to reach all of them", raised)
	}
	if narrated < 3 {
		t.Errorf("only %d narrations across the whole fixture set — the "+
			"edge-trigger is untested", narrated)
	}
	if wroteFile < 20 {
		t.Errorf("only %d fixtures wrote a snapshot file", wroteFile)
	}
}

// sameJSON compares two JSON documents semantically. It exists because the
// two runtimes agree on the VALUE and not always on the spelling: CPython
// writes 1 where encoding/json writes 1 for an int and json.Number("1") for
// a parsed one, and neither difference is a port defect.
func sameJSON(t *testing.T, a, b string) bool {
	t.Helper()
	norm := func(s string) any {
		var v any
		d := json.NewDecoder(strings.NewReader(s))
		d.UseNumber()
		if err := d.Decode(&v); err != nil {
			t.Fatalf("not JSON: %v\n%s", err, s)
		}
		return canon(v)
	}
	x, _ := json.Marshal(norm(a))
	y, _ := json.Marshal(norm(b))
	return string(x) == string(y)
}

// canon normalises numbers so that 1, 1.0 and json.Number("1") compare
// equal, and recurses. Everything else is left alone — in particular STRING
// content, which is where every real divergence in this chunk lives.
func canon(v any) any {
	switch t := v.(type) {
	case map[string]any:
		for k, e := range t {
			t[k] = canon(e)
		}
		return t
	case []any:
		for i, e := range t {
			t[i] = canon(e)
		}
		return t
	case json.Number:
		if f, err := t.Float64(); err == nil {
			return f
		}
		return t.String()
	case float64:
		return t
	case int:
		return float64(t)
	}
	return v
}

// TestWriteSnapshotRoundTripsThroughLoad pins the pair the differential
// cannot: the probe seeds files with `json.dumps(prior)` and reads them back
// as text, so nothing there proves the Go WRITER's output is something the
// Go READER accepts. A snapshot that cannot be re-loaded turns every cycle
// after the first into a first cycle, silently.
func TestWriteSnapshotRoundTripsThroughLoad(t *testing.T) {
	ws := t.TempDir()
	if got := LoadSnapshot(ws); len(got) != 0 {
		t.Fatalf("an empty workspace loaded %v", got)
	}
	snap := syObj(t, `{"cycle": 7, "processes": {"p": {"status": "OK", "é": [1, {}]}}}`)
	if err := WriteSnapshot(ws, snap); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(SnapshotPath(ws))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(string(raw), "}\n") {
		t.Errorf("the snapshot must end with exactly one newline: %q", string(raw))
	}
	back := LoadSnapshot(ws)
	a, _ := json.Marshal(syGo(snap))
	b, _ := json.Marshal(syGo(back))
	if !sameJSON(t, string(a), string(b)) {
		t.Errorf("round trip lost something:\n  wrote %s\n  read  %s", a, b)
	}
}

// TestRunCycleReadsTheClockOncePerDeclarationPlusOnce pins the SHAPE the
// frozen-clock differential cannot see (L48). Freezing time is what makes
// the fixtures comparable and is also what would let a port that reads the
// clock once, or twice, or fifty times pass every one of them.
func TestRunCycleReadsTheClockOncePerDeclarationPlusOnce(t *testing.T) {
	for _, n := range []int{0, 1, 3} {
		decls := make([]Declaration, n)
		for i := range decls {
			decls[i] = Declaration{Name: fmt.Sprintf("p%d", i),
				Probe: func(pyval.Obj) (string, string, pyval.Obj, error) {
					return OK, "e", pyval.Obj{}, nil
				}}
		}
		reads := 0
		_, _, _ = RunCycle(pyval.Obj{}, decls, true, func() string {
			reads++
			return syFrozen
		})
		if reads != n+1 {
			t.Errorf("%d declarations read the clock %d times, want %d "+
				"(one per declaration, one for updated_at)", n, reads, n+1)
		}
	}
}

// TestLoadSnapshotTreatsUndecodableBytesAsNoSnapshot pins the one
// LoadSnapshot lane the differential cannot reach: every fixture travels to
// CPython as JSON, and JSON cannot carry a lone 0x80. Python's
// `path.read_text(encoding="utf-8")` raises UnicodeDecodeError inside the
// same try that swallows a parse failure, so the answer is `{}` — verified
// by reading the source, not by a fixture, and pinned here so the Go side
// cannot quietly start doing Go's replacement-character thing instead.
func TestLoadSnapshotTreatsUndecodableBytesAsNoSnapshot(t *testing.T) {
	ws := t.TempDir()
	path := SnapshotPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	// Valid JSON structurally; invalid UTF-8 inside the string.
	if err := os.WriteFile(path, []byte("{\"a\": \"\x80\"}"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := LoadSnapshot(ws); len(got) != 0 {
		t.Errorf("undecodable bytes loaded as %v, want an empty snapshot", got)
	}
}

// TestAFailingProbeContributesNothingButItsMessage pins a contract the
// differential structurally cannot reach. Python's probe either returns a
// triple or raises — there is no third state — so a fixture cannot express
// "raised AND handed back an observation". Go's Probe signature can, because
// an error is a fourth return value rather than a control transfer, and a
// port that kept `obs` on the error path would silently stamp a partial
// observation into the ring buffer. The seam invented the hazard; the seam
// has to pin it.
func TestAFailingProbeContributesNothingButItsMessage(t *testing.T) {
	junk := pyval.Obj{}
	junk.Set("half", "written")
	decl := Declaration{Name: "p", Probe: func(pyval.Obj) (string, string, pyval.Obj, error) {
		return OK, "looked fine", junk, fmt.Errorf("boom")
	}}
	next, told, summary := RunCycle(pyval.Obj{}, []Declaration{decl}, true,
		func() string { return syFrozen })
	if len(told) != 0 {
		t.Errorf("a failing probe narrated %v", told)
	}
	if summary.Ran != 1 {
		t.Errorf("ran = %d, want 1 — a failing probe still ran", summary.Ran)
	}
	procs, _ := next.Get("processes")
	entry, _ := procs.(pyval.Obj).Get("p")
	e := entry.(pyval.Obj)
	if st, _ := e.Get("status"); st != Unknown {
		t.Errorf("status = %v, want UNKNOWN", st)
	}
	if ev, _ := e.Get("evidence"); ev != "probe failed: boom" {
		t.Errorf("evidence = %q", ev)
	}
	h, _ := e.Get("history")
	if lst, _ := h.(pyval.List); len(lst) != 0 {
		t.Errorf("a failing probe wrote history: %v", lst)
	}
}

// TestRunCycleDoesNotMutateTheProbesObservation pins the other hazard the
// seam invents. Python's `{**obs, "at": now}` BUILDS a new dict; a Go port
// that writes `stamped := obs` aliases the probe's own value and stamps it
// in place. A probe that returns a package-level or cached Obj — which the
// real ones will, they read a store once — would find it growing an "at"
// key it never set, and the second cycle would then see a probe-supplied
// "at" that came from the first.
func TestRunCycleDoesNotMutateTheProbesObservation(t *testing.T) {
	// The observation already carries an "at", which is the lane that
	// matters: Obj.Set REPLACES an existing key in place, so an aliased
	// slice is corrupted through the shared backing array. Setting a NEW
	// key appends instead, and append on a full slice reallocates — which
	// is why a fixture without "at" lets the aliasing bug hide.
	obs := pyval.Obj{}
	obs.Set("at", "1999")
	obs.Set("n", 1)
	decl := Declaration{Name: "p", Probe: func(pyval.Obj) (string, string, pyval.Obj, error) {
		return OK, "e", obs, nil
	}}
	RunCycle(pyval.Obj{}, []Declaration{decl}, true, func() string { return syFrozen })
	if at, _ := obs.Get("at"); at != "1999" {
		t.Errorf("the cycle stamped the probe's own observation: at = %v", at)
	}
	if len(obs) != 2 {
		t.Errorf("the probe's observation grew to %v", obs)
	}
}

// TestRunCycleKeepsEveryProcessItAppended is the regression pin for the
// slice-header rule in RunCycle: Obj is a slice, so storing `processes`
// into the snapshot BEFORE the loop keeps a header that the loop's appends
// then outgrow.
//
// It is not the only guard — verified by hand-editing a copy into exactly
// that shape, the differential fails too, on every cycle fixture at once
// ("processes": {} against CPython's populated map). This test earns its
// place by naming the cause in one line instead of thirty-seven diffs, and
// by running in microseconds without an interpreter.
func TestRunCycleKeepsEveryProcessItAppended(t *testing.T) {
	decls := make([]Declaration, 5)
	for i := range decls {
		decls[i] = Declaration{Name: fmt.Sprintf("p%d", i),
			Probe: func(pyval.Obj) (string, string, pyval.Obj, error) {
				return OK, "e", pyval.Obj{}, nil
			}}
	}
	next, _, _ := RunCycle(pyval.Obj{}, decls, true, func() string { return syFrozen })
	v, ok := next.Get("processes")
	if !ok {
		t.Fatal("no processes key")
	}
	procs, ok := v.(pyval.Obj)
	if !ok {
		t.Fatalf("processes is %T", v)
	}
	if len(procs) != len(decls) {
		t.Fatalf("the snapshot kept %d of %d processes", len(procs), len(decls))
	}
}
