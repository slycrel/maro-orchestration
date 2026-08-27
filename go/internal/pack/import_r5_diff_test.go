package pack

import (
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The r5 review's findings, each with the fixture it had no fixture for.
//
// Every one of these tests failed before its fix and passes after; the
// existing import differential passed throughout, which is the point. A
// fixture is written against the case that was ALREADY handled, so the
// evidence a review produces is not the finding — it is the input the suite
// could not have produced on its own.

// TestImportAcceptsCPythonNonFiniteRows — H1.
//
// CPython's `json.dumps` writes bare `NaN`/`Infinity` BY DEFAULT
// (allow_nan=True), and knowledge_web appends lesson rows with a plain
// `json.dumps(asdict(tl))`. So a workspace CPython wrote can hold a row
// whose numbers Go's decoder refuses outright — and all three pack trust
// lanes then dropped the row with no imported row, no `malformed_skipped`
// report row, and no warning.
//
// The row here carries a non-finite in a field NOBODY READS. That is
// deliberate: the loss was never proportional to the bad value's importance,
// because the refusal killed the whole document.
func TestImportAcceptsCPythonNonFiniteRows(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("memory/long/lessons.jsonl",
			`{"lesson_id":"l1","lesson":"survives a NaN","source_goal":"port",`+
				`"confidence":0.7,"tier":"long","score":1.4,"minted_from":"outcome",`+
				`"decay_debug":NaN}`+"\n")
		w("memory/hypotheses.jsonl",
			`{"hyp_id":"h1","lesson":"survives an Infinity","domain":"ops",`+
				`"confirmations":2,"contradictions":0,"drift":Infinity}`+"\n"+
				`{"hyp_id":"h2","lesson":"the control","domain":"ops",`+
				`"confirmations":1,"contradictions":0}`+"\n")
	}
	want, got, goTarget := runImportBoth(t, seed, nil)
	if len(want.Lessons) == 0 || len(want.Hypotheses) < 2 {
		t.Fatalf("the fixture is not exercising the case: CPython imported "+
			"%d lessons and %d hypotheses", len(want.Lessons), len(want.Hypotheses))
	}
	cmpResultRows(t, "lessons", got.LessonsImported, want.Lessons)
	cmpResultRows(t, "hypotheses", got.HypothesesImported, want.Hypotheses)
	cmpStoreBytes(t, goTarget, want, "memory/medium/lessons.jsonl")
	cmpStoreBytes(t, goTarget, want, "memory/hypotheses.jsonl")
}

// TestImportStampsAbsentAndNullFieldsLikePython — H2 and H4 together,
// because they are one idiom at two sites and separating them would let a
// fix for either read as a fix for both.
//
//   - `original_tier` is `row.get("tier", "")`: ABSENT gives "", a present
//     null gives null. A fix that dropped a wrong `str()` replaced it with
//     `row["tier"]`, which is nil for both — so absent started writing
//     `null` into a shared store.
//   - `task_type`/`outcome` are `str(row.get(k, ""))`: absent gives "", a
//     present null gives the literal string "None".
//
// Four rows, so absent and null are separated on BOTH fields at once. A
// fixture that seeds only values (the existing one seeds `"tier":5` and
// `"tier":"long"`) is blind to the entire question.
func TestImportStampsAbsentAndNullFieldsLikePython(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("memory/long/lessons.jsonl",
			// tier absent, task_type/outcome absent
			`{"lesson_id":"a","lesson":"absent everywhere","source_goal":"port",`+
				`"confidence":0.7,"score":1.4,"minted_from":"outcome"}`+"\n"+
				// tier null, task_type/outcome null
				`{"lesson_id":"b","lesson":"null everywhere","source_goal":"port",`+
				`"confidence":0.7,"score":1.4,"minted_from":"outcome",`+
				`"tier":null,"task_type":null,"outcome":null}`+"\n"+
				// tier present as a number, the case the old fixture had
				`{"lesson_id":"c","lesson":"tier as a number","source_goal":"port",`+
				`"confidence":0.7,"score":1.4,"minted_from":"outcome",`+
				`"tier":5,"task_type":"build","outcome":"ok"}`+"\n"+
				// task_type present but FALSY — str(0) is "0", not "". A
				// truthiness guard here would agree with the absent case
				// and look correct.
				`{"lesson_id":"d","lesson":"a falsy task type","source_goal":"port",`+
				`"confidence":0.7,"score":1.4,"minted_from":"outcome",`+
				`"tier":"","task_type":0,"outcome":false}`+"\n")
	}
	want, got, goTarget := runImportBoth(t, seed, nil)
	if len(want.Lessons) != 4 {
		t.Fatalf("CPython imported %d of 4 lessons — the fixture is not "+
			"exercising the case", len(want.Lessons))
	}
	stored := want.Stores["memory/medium/lessons.jsonl"]
	for _, marker := range []string{`"original_tier": ""`, `"original_tier": null`,
		`"task_type": "None"`, `"task_type": "0"`} {
		if !strings.Contains(stored, marker) {
			t.Fatalf("CPython did not write %s — this fixture is not "+
				"measuring what it claims:\n%s", marker, stored)
		}
	}
	cmpResultRows(t, "lessons", got.LessonsImported, want.Lessons)
	cmpStoreBytes(t, goTarget, want, "memory/medium/lessons.jsonl")
}

// TestImportReportsPythonsFloatErrors — M1.
//
// The error text rides a `malformed_skipped` result row into a shared audit
// ledger, so it is a content key. Four branches, four different CPython
// sentences — a ValueError that reprs the offending string, and a TypeError
// that names the Python type. `not a number: map[]` was Go's %v leaking
// into a row an operator reads.
func TestImportReportsPythonsFloatErrors(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("memory/long/lessons.jsonl",
			`{"lesson_id":"s","lesson":"a string score","source_goal":"port",`+
				`"tier":"long","minted_from":"outcome","score":"abc","confidence":0.5}`+"\n"+
				`{"lesson_id":"l","lesson":"a list confidence","source_goal":"port",`+
				`"tier":"long","minted_from":"outcome","score":1.4,"confidence":[]}`+"\n"+
				`{"lesson_id":"d","lesson":"a dict score","source_goal":"port",`+
				`"tier":"long","minted_from":"outcome","score":{},"confidence":0.5}`+"\n"+
				`{"lesson_id":"n","lesson":"a null score","source_goal":"port",`+
				`"tier":"long","minted_from":"outcome","score":null,"confidence":0.5}`+"\n"+
				// A quote inside the value, so repr's quote-switching is
				// part of the comparison rather than an untested branch.
				`{"lesson_id":"q","lesson":"a quoted score","source_goal":"port",`+
				`"tier":"long","minted_from":"outcome","score":"it's bad","confidence":0.5}`+"\n")
	}
	want, got, _ := runImportBoth(t, seed, nil)
	skipped := 0
	for _, r := range want.Lessons {
		if r["outcome"] == "malformed_skipped" {
			skipped++
		}
	}
	if skipped != 5 {
		t.Fatalf("CPython reported %d of 5 rows malformed_skipped — the "+
			"fixture is not exercising the error path: %+v", skipped, want.Lessons)
	}
	cmpResultRows(t, "lessons", got.LessonsImported, want.Lessons)
}

// TestImportRefusesToWriteANonFiniteCPythonWrites — L3, and the layer under
// it.
//
// The fix here was to asFloat: `1e400` is already `inf` by the time
// CPython's `float()` sees it and `float(inf)` succeeds, where Go's
// `json.Number.Float64` reported the same +/-Inf alongside ErrRange and the
// port treated that as a coercion fault. The values agreed; only the error
// did not.
//
// Fixing it exposed the real divergence one layer down, and it is a
// DELIBERATE one that must not be "fixed" into parity:
//
//   - CPython imports the row and writes a bare `Infinity` into
//     memory/medium/lessons.jsonl, because knowledge_web appends with a
//     plain `json.dumps(asdict(tl))` and never calls `prove_record_line`.
//   - `prove_record_line` exists in that same codebase precisely to stop
//     this: it dumps with `allow_nan=False` and re-reads through
//     `loads_clean`, whose `parse_constant=_refuse_constant` REFUSES the
//     bare tokens. So the row CPython just wrote is a row CPython's own
//     strict readers will not read back. It is stranded on write.
//   - The port refuses at the writer (pyjson.RefuseNonFinite) and reports
//     `malformed_skipped`, which costs the row and keeps the store
//     readable.
//
// Refusing is the safe direction and the port stays on it. This test pins
// the disagreement EXPLICITLY so it cannot drift into an accident: if
// either side ever changes, this fails and someone has to decide again.
//
// Note the asymmetry with TestImportAcceptsCPythonNonFiniteRows above,
// which is not a contradiction: READING a non-finite must succeed, because
// CPython writes them and refusing the read loses the whole document.
// WRITING one is where the port declines.
func TestImportRefusesToWriteANonFiniteCPythonWrites(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("memory/long/lessons.jsonl",
			`{"lesson_id":"big","lesson":"an enormous score","source_goal":"port",`+
				`"tier":"long","minted_from":"outcome","score":1e400,"confidence":0.5}`+"\n"+
				`{"lesson_id":"fine","lesson":"the finite control","source_goal":"port",`+
				`"tier":"long","minted_from":"outcome","score":1.4,"confidence":0.5}`+"\n")
	}
	want, got, _ := runImportBoth(t, seed, nil)

	byID := map[string]map[string]any{}
	for _, r := range want.Lessons {
		byID[asString(r["lesson_id"])] = r
	}
	if byID["big"]["outcome"] != "imported_medium" {
		t.Fatalf("CPython did not import the out-of-range row — the premise "+
			"of this test has changed: %+v", byID["big"])
	}
	if !strings.Contains(want.Stores["memory/medium/lessons.jsonl"], "Infinity") {
		t.Fatal("CPython did not write a bare Infinity into the shared store " +
			"— the divergence this test pins no longer exists")
	}

	goByID := map[string]pyval.Obj{}
	for _, r := range got.LessonsImported {
		goByID[r.GetString("lesson_id")] = r
	}
	if goByID["big"].GetString("outcome") != "malformed_skipped" {
		t.Errorf("the port imported a row it refuses to write: %+v", goByID["big"])
	}
	// WHICH refusal matters. asFloat used to reject the literal itself
	// ("value out of range"), which is a coercion fault CPython does not
	// have; the row must now reach the writer and be refused THERE, for the
	// reason the writer actually has. Asserting only the outcome would let
	// the coercion fault come back invisibly — the two spellings agree on
	// the verdict and disagree about the program.
	if e := goByID["big"].GetString("error"); e != "non-finite number refused" {
		t.Errorf("refused for the wrong reason: %q — asFloat should coerce "+
			"1e400 to +Inf like CPython's float(), leaving the writer to "+
			"decline it", e)
	}
	// The refusal must be scoped to the offending row. A writer that gave
	// up on the batch would satisfy the assertion above and lose the rest.
	if goByID["fine"].GetString("outcome") != "imported_medium" {
		t.Errorf("the finite control row did not import: %+v", goByID["fine"])
	}
	if byID["fine"]["outcome"] != goByID["fine"].GetString("outcome") {
		t.Errorf("the finite control diverges too (%v vs CPython %v) — the "+
			"divergence is wider than the non-finite row",
			goByID["fine"].GetString("outcome"), byID["fine"]["outcome"])
	}
}

// pyFloatSrc is `float(json.loads(literal))` — the two steps a pack row's
// numeric field takes on the CPython side, in that order.
const pyFloatSrc = `
import json, sys
lit = json.loads(sys.argv[1])["lit"]
try:
    v = json.loads(lit)
except Exception as exc:
    print(json.dumps({"err": "%s" % exc})); raise SystemExit
try:
    f = float(v)
except Exception as exc:
    print(json.dumps({"err": str(exc), "cls": type(exc).__name__})); raise SystemExit
print(json.dumps({"repr": repr(f)}))
`

// TestAsFloatMatchesPythonFloat pins asFloat directly, because the pack
// pipeline cannot reach two of its branches.
//
// Both exporters normalize a float literal through loads→dumps before it
// enters a pack, so an out-of-range literal arrives as the token `Infinity`
// — and json.Number("Infinity").Float64() succeeds, skipping the ErrRange
// branch the L3 fix added. Only a HAND-BUILT pack carries `1e400` through,
// which is precisely the input this file treats as adversarial.
//
// A measurement is only evidence about the call you actually made (lens
// 14). Driving this through the exporter would have measured the exporter.
func TestAsFloatMatchesPythonFloat(t *testing.T) {
	for _, lit := range []string{
		"1e400", "-1e400", "1e-400", "0.7", "42", "true", "false", "null",
		`"0.7"`, `"abc"`, `"it's bad"`, "[]", "{}", "1e308",
	} {
		t.Run(lit, func(t *testing.T) {
			var want struct {
				Repr string `json:"repr"`
				Err  string `json:"err"`
				Cls  string `json:"cls"`
			}
			pyprobe.Probe{Stdlib: true}.RunJSON(t, pyFloatSrc, &want,
				pyprobe.Arg(t, map[string]any{"lit": lit}))

			row, derr := pyval.LoadsMap(`{"v":` + lit + `}`)
			if derr != nil {
				t.Fatalf("the port could not even decode %s: %v", lit, derr)
			}
			f, err := asFloat(row["v"], 0)

			if want.Err != "" {
				if err == nil {
					t.Fatalf("asFloat accepted %s (%v); CPython raised %s: %s",
						lit, f, want.Cls, want.Err)
				}
				if err.Error() != want.Err {
					t.Errorf("asFloat(%s) error %q, CPython %q", lit, err, want.Err)
				}
				return
			}
			if err != nil {
				t.Fatalf("asFloat(%s) failed with %v; CPython gives %s",
					lit, err, want.Repr)
			}
			if got := pyjson.FloatRepr(f); got != want.Repr {
				t.Errorf("asFloat(%s) = %s, CPython float() = %s",
					lit, got, want.Repr)
			}
		})
	}
}
