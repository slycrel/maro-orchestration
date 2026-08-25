package skills

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pyVariantSrc seeds a skills store and runs retire_losing_variants, then
// reports the answer AND every file it touched.
//
// PYTHONHASHSEED is pinned by the caller, not here — the function iterates a
// SET of parent ids, so its returned ORDER is a property of the interpreter's
// string hashing rather than of the algorithm. The comparison below treats
// both lists as sets for that reason and says so.
const pyVariantSrc = `
import json, os, sys
import skills

_argv = json.loads(sys.argv[1])

p = skills._skills_path()
p.parent.mkdir(parents=True, exist_ok=True)
from skill_types import dict_to_skill, skill_to_dict
with p.open("w", encoding="utf-8") as fh:
    for row in _argv["rows"]:
        # content_hash is REQUIRED for admission (validate_skill_row: "absent
        # is not empty, and neither is proof"). A fixture without one is
        # skipped by load_skills, which is how the first cut of this test
        # compared two empty results and called it agreement.
        row = dict(row)
        row["content_hash"] = skills.compute_skill_hash(dict_to_skill(row))
        fh.write(json.dumps(row) + "\n")

if _argv.get("stats"):
    sp = skills._skill_stats_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    with sp.open("w", encoding="utf-8") as fh:
        for row in _argv["stats"]:
            fh.write(json.dumps(row) + "\n")

def _read(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""

# Measured BEFORE the call, because retirement REWRITES this file: read
# afterwards it reports the surviving pool, and a case that retires one of two
# rows would look like a fixture one row of which was never admissible. That
# is what the first cut of the guard did, and it turned an anti-vacuity check
# into a false alarm on exactly the cases doing the most work.
_loaded = len(skills.load_skills())

try:
    res = skills.retire_losing_variants(dry_run=_argv["dry_run"],
                                        min_uses=_argv["min_uses"])
    out = {"ok": True, "promoted": sorted(res["promoted"]),
           "retired": sorted(res["retired"])}
except BaseException as e:
    out = {"ok": False, "cls": type(e).__name__, "msg": str(e)}

out["loaded"] = _loaded
out["skills"] = _read(p)
out["archive"] = _read(skills._skills_archive_path())
prov = skills._skills_path().parent / "skill_provenance"
out["provenance"] = sorted(q.name.split("_")[0] for q in prov.glob("*.json")) \
    if prov.exists() else []
print(json.dumps(out, sort_keys=True))
`

// pyVariantCreateSrc is create_skill_variant, which has one behaviour worth
// a differential of its own: the self-referential refusal, and the exact
// ValueError sentence a caller's `except ValueError as e` prints.
const pyVariantCreateSrc = `
import json, sys
import skills
from skill_types import dict_to_skill

_argv = json.loads(sys.argv[1])
orig = dict_to_skill(_argv["original"])
rew = dict_to_skill(_argv["rewritten"])
try:
    out = skills.create_skill_variant(orig, rew)
    res = {"ok": True, "variant_of": out.variant_of,
           "variant_wins": out.variant_wins,
           "variant_losses": out.variant_losses}
except BaseException as e:
    res = {"ok": False, "cls": type(e).__name__, "msg": str(e)}
print(json.dumps(res, sort_keys=True))
`

func TestCreateSkillVariantMatchesCPython(t *testing.T) {
	for _, c := range []struct {
		name       string
		origID     string
		rewID      string
		priorWins  int
		priorLoss  int
		priorOfSet bool
	}{
		{"a distinct challenger", "parent1", "child1", 0, 0, false},
		// The counters are RESET, not carried: a rewritten skill that
		// already held a variant record starts its A/B at zero.
		{"prior counters are reset", "parent1", "child1", 7, 3, false},
		// A challenger already pointing somewhere else is re-pointed.
		{"an existing variant_of is overwritten", "parent1", "child1", 0, 0, true},
		// The refusal, and its exact sentence.
		{"a self-referential challenger is refused", "same", "same", 0, 0, false},
		{"a short id in the message", "ab", "ab", 0, 0, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			orig := map[string]any{"id": c.origID, "name": "parent skill",
				"description": "d"}
			rew := map[string]any{"id": c.rewID, "name": "child skill",
				"description": "d2", "variant_wins": c.priorWins,
				"variant_losses": c.priorLoss}
			if c.priorOfSet {
				rew["variant_of"] = "someone-else"
			}
			var want struct {
				OK        bool    `json:"ok"`
				VariantOf *string `json:"variant_of"`
				Wins      int     `json:"variant_wins"`
				Losses    int     `json:"variant_losses"`
				Cls       string  `json:"cls"`
				Msg       string  `json:"msg"`
			}
			pyprobe.Probe{Marker: "skills.py"}.RunJSON(t, pyVariantCreateSrc,
				&want, pyprobe.Arg(t, map[string]any{
					"original": orig, "rewritten": rew}))

			goOrig := skillOf(t, orig)
			goRew := skillOf(t, rew)
			got, gotErr := CreateSkillVariant(goOrig, &goRew, nil)

			// Python MUTATES the argument and returns that same object, so
			// the caller's own copy must carry the markers too — checked
			// here rather than only through the return value, because
			// taking `rewritten` by value passed every assertion below
			// while leaving a caller who followed Python's shape holding an
			// unmarked skill (round 10 LOW).
			if gotErr == nil {
				if goRew.VariantOf == nil || *goRew.VariantOf != goOrig.ID {
					t.Errorf("the ARGUMENT was not mutated: variant_of=%v, "+
						"want %q — Python assigns onto the caller's object",
						pyOptStr(goRew.VariantOf), goOrig.ID)
				}
				if goRew.VariantWins != 0 || goRew.VariantLosses != 0 {
					t.Errorf("the ARGUMENT kept wins=%d losses=%d; Python zeroes "+
						"both on the caller's object", goRew.VariantWins,
						goRew.VariantLosses)
				}
			}

			if want.OK != (gotErr == nil) {
				t.Fatalf("ok=%v; CPython ok=%v (%s: %s)",
					gotErr == nil, want.OK, want.Cls, want.Msg)
			}
			if !want.OK {
				if cls := pyval.ClassOf(gotErr); cls != want.Cls {
					t.Errorf("raises %s, CPython raises %s", cls, want.Cls)
				}
				if msg := gotErr.Error(); msg != want.Msg {
					t.Errorf("message\n go: %q\n py: %q", msg, want.Msg)
				}
				return
			}
			gotOf := ""
			if got.VariantOf != nil {
				gotOf = *got.VariantOf
			}
			wantOf := ""
			if want.VariantOf != nil {
				wantOf = *want.VariantOf
			}
			if gotOf != wantOf {
				t.Errorf("variant_of = %q, CPython says %q", gotOf, wantOf)
			}
			if got.VariantWins != want.Wins || got.VariantLosses != want.Losses {
				t.Errorf("counters = %d/%d, CPython says %d/%d",
					got.VariantWins, got.VariantLosses, want.Wins, want.Losses)
			}
		})
	}
}

// TestRetireLosingVariantsMatchesCPython pins the A/B resolution, its FILES
// included.
//
// The file comparison is the half that matters. retire_losing_variants
// returns two id lists, but what it DOES is rewrite the live pool, append to
// an archive that must never lose a row, and drop a provenance sidecar per
// loser. A port could return the right ids while archiving nothing — which
// is the retention decree's exact failure — and a return-value-only
// differential would call that agreement.
func TestRetireLosingVariantsMatchesCPython(t *testing.T) {
	// A parent with enough trials for the gate, at a utility the
	// challenger's rate can straddle.
	parent := func(id string, utility float64, uses int) map[string]any {
		return map[string]any{"id": id, "name": "parent-" + id,
			"description": "parent desc", "steps_template": []any{"p1"},
			"trigger_patterns": []any{"pt"}, "utility_score": utility,
			"use_count": uses, "optimization_objective": "speed",
			// created_at and content_hash are BOTH required by
			// validate_skill_row — absence is an absence of proof, not a
			// default — and a row missing either is skipped by load_skills
			// rather than refused loudly. The hash is stamped by the
			// seeder; the timestamp has to be in the fixture.
			"created_at": "2026-01-01T00:00:00+00:00"}
	}
	child := func(id, of string, wins, losses int) map[string]any {
		return map[string]any{"id": id, "name": "child-" + id,
			"description": "child desc", "steps_template": []any{"c1", "c2"},
			"trigger_patterns": []any{"ct"}, "variant_of": of,
			"variant_wins": wins, "variant_losses": losses,
			"optimization_objective": "accuracy",
			"created_at":             "2026-01-02T00:00:00+00:00"}
	}

	for _, c := range []struct {
		name    string
		rows    []map[string]any
		stats   []map[string]any
		dryRun  bool
		minUses int
	}{
		{"no variants at all", []map[string]any{parent("p1", 0.5, 10)},
			nil, false, 5},

		// Below the gate on either side does nothing.
		{"challenger short of min_uses", []map[string]any{
			parent("p1", 0.4, 10), child("c1", "p1", 2, 1)}, nil, false, 5},
		{"parent short of min_uses", []map[string]any{
			parent("p1", 0.4, 2), child("c1", "p1", 5, 1)}, nil, false, 5},

		// The parent's trials come from STATS when use_count is legacy-zero
		// — the row that separates a port reading only use_count.
		{"parent trials come from live stats", []map[string]any{
			parent("p1", 0.4, 0), child("c1", "p1", 5, 1)},
			[]map[string]any{{"skill_id": "p1", "total_uses": 20}}, false, 5},

		// Challenger beats the parent: content copied up, challenger
		// archived, hash recomputed.
		{"challenger wins", []map[string]any{
			parent("p1", 0.4, 10), child("c1", "p1", 5, 1)}, nil, false, 5},
		// Parent wins outright.
		{"parent wins", []map[string]any{
			parent("p1", 0.9, 10), child("c1", "p1", 5, 5)}, nil, false, 5},
		// A TIE goes to the parent — strictly-greater, so 0.5 vs 0.5 loses.
		{"an exact tie retires the challenger", []map[string]any{
			parent("p1", 0.5, 10), child("c1", "p1", 5, 5)}, nil, false, 5},

		// Two challengers, one on each side of the parent's rate.
		{"two challengers split", []map[string]any{
			parent("p1", 0.5, 10), child("c1", "p1", 9, 1),
			child("c2", "p1", 1, 9)}, nil, false, 5},

		// dry_run changes nothing on disk and still reports.
		{"dry run reports without writing", []map[string]any{
			parent("p1", 0.4, 10), child("c1", "p1", 5, 1)}, nil, true, 5},

		// A challenger whose parent is gone is skipped entirely.
		{"an orphaned challenger", []map[string]any{
			child("c1", "missing", 5, 1)}, nil, false, 5},

		// The self-referential heal — the corrupt shape that would
		// otherwise archive-and-delete the parent.
		{"a self-referential variant is healed, not retired",
			[]map[string]any{selfVariant("s1")}, nil, false, 5},
		{"a self-referential variant is healed in dry run too",
			[]map[string]any{selfVariant("s1")}, nil, true, 5},

		// min_uses=0 makes the max(c_total,1) guard reachable: a challenger
		// with NO trials at all is judged, and 0/1 is a rate of zero.
		{"min_uses zero reaches the divide guard", []map[string]any{
			parent("p1", 0.0, 0), child("c1", "p1", 0, 0)}, nil, false, 0},

		// A variant CHAIN: c1 challenges p1 and c2 challenges c1. Both
		// challengers lose here — a middle row's utility_score defaults to
		// 1.0, which no challenger rate can exceed — so this case pins the
		// chain's SHAPE but not the updated_ids arithmetic. It was written
		// believing it did; the mutation battery said otherwise, and the row
		// below is the one that actually does.
		{"a variant chain", []map[string]any{
			parent("p1", 0.1, 10), child("c1", "p1", 9, 1),
			child("c2", "c1", 9, 1)},
			[]map[string]any{{"skill_id": "c1", "total_uses": 20}}, false, 5},

		// The case updated_ids' set-DIFFERENCE exists for: c1 is promoted
		// (c2's 0.9 beats c1's 0.1) and retired in the same pass (c1's 0.9
		// loses to p1's 0.95), so it is a drop, not a write. A union here
		// names an id absent from the surviving pool, and SaveSkills refuses
		// the whole rewrite — the pool keeps its losers and the report still
		// claims they were retired.
		{"a promoted parent that is itself retired", []map[string]any{
			parent("p1", 0.95, 10),
			withField(child("c1", "p1", 9, 1), "utility_score", 0.1),
			child("c2", "c1", 9, 1)},
			[]map[string]any{{"skill_id": "c1", "total_uses": 20}}, false, 5},

		// The two rows that pin the DENOMINATOR. `max(c_total, 1)` must
		// divide by the trial count itself, and every other fixture's
		// margin is wide enough to survive an off-by-one in it: 5 wins and
		// no losses is a rate of 1.0, which clears a parent at 0.9, while
		// 5/(5+1) does not. Its sibling straddles from the other side.
		{"a challenger wins only at the true denominator", []map[string]any{
			parent("p1", 0.9, 10), child("c1", "p1", 5, 0)}, nil, false, 5},
		{"...and loses at it", []map[string]any{
			parent("p1", 0.9, 10), child("c1", "p1", 4, 1)}, nil, false, 5},

		// A CHAIN WHERE BOTH LINKS WIN — the case that makes CPython
		// disagree with itself, and the reason the seed sweep below exists.
		//
		// The other chain fixture above has its middle link LOSE, so only
		// one promotion happens and group order cannot matter. Here c1 beats
		// p1 AND c2 beats c1, so c1 is in `promoted` and `retired` both, and
		// the content p1 SURVIVES with depends on which group ran first:
		// "c1 desc" if p1's group won the race to read c1, "c2 desc" if c1's
		// group overwrote c1 first. The descriptions are overridden for
		// exactly that reason — child() gives every child the same text, and
		// with identical content the two orders produce the same file and
		// this fixture would prove nothing (lens 12).
		{"a chain where BOTH links win", []map[string]any{
			parent("p1", 0.1, 10),
			withField(withField(child("c1", "p1", 9, 1),
				"utility_score", 0.5), "description", "c1 desc"),
			withField(child("c2", "c1", 10, 0), "description", "c2 desc")},
			[]map[string]any{{"skill_id": "c1", "total_uses": 20}}, false, 5},
	} {
		t.Run(c.name, func(t *testing.T) {
			pyWS := t.TempDir()
			goWS := t.TempDir()

			arg := map[string]any{"rows": c.rows, "stats": c.stats,
				"dry_run": c.dryRun, "min_uses": c.minUses}
			var want struct {
				OK         bool     `json:"ok"`
				Promoted   []string `json:"promoted"`
				Retired    []string `json:"retired"`
				Loaded     int      `json:"loaded"`
				Skills     string   `json:"skills"`
				Archive    string   `json:"archive"`
				Provenance []string `json:"provenance"`
				Cls        string   `json:"cls"`
				Msg        string   `json:"msg"`
			}
			pyprobe.Probe{Marker: "skills.py", Workspace: pyWS}.RunJSON(
				t, pyVariantSrc, &want, pyprobe.Arg(t, arg))
			if !want.OK {
				t.Fatalf("CPython raised %s: %s", want.Cls, want.Msg)
			}

			// THE GUARD THIS TEST WAS MISSING. Every row must be
			// ADMISSIBLE — validate_skill_row refuses one without a
			// content_hash — or load_skills silently returns nothing and
			// both runtimes agree that an empty pool retires nothing. The
			// first cut of this table did exactly that: twelve cases green
			// while the mutation battery caught two of twelve.
			if want.Loaded != len(c.rows) {
				t.Fatalf("CPython loaded %d of %d seeded rows; the fixture is "+
					"not admissible and this case would compare two empty "+
					"answers", want.Loaded, len(c.rows))
			}
			seedStore(t, goWS, c.rows, c.stats)
			if n := len(LoadSkills(goWS).Skills); n != len(c.rows) {
				t.Fatalf("the port loaded %d of %d seeded rows", n, len(c.rows))
			}
			got, err := RetireLosingVariants(goWS, c.dryRun, c.minUses, nil)
			if err != nil {
				t.Fatalf("RetireLosingVariants: %v", err)
			}

			// SETS, deliberately: CPython's order comes from iterating a
			// set of parent ids, so it is not stable across interpreter
			// runs and is not part of the contract.
			if !sameSet(got.Promoted, want.Promoted) {
				t.Errorf("promoted = %v, CPython says %v",
					sortedCopy(got.Promoted), want.Promoted)
			}
			if !sameSet(got.Retired, want.Retired) {
				t.Errorf("retired = %v, CPython says %v",
					sortedCopy(got.Retired), want.Retired)
			}

			// The FILES. Rows are compared as decoded objects because the
			// live pool's row ORDER follows each runtime's rewrite and is
			// not a contract; the SET of rows and every field in them is.
			//
			// A skill in BOTH lists is a variant chain whose links both won,
			// and for those CPython has more than one right answer — see
			// RetireLosingVariants' ordering note. Strict equality against
			// ONE run of CPython would then pass or fail on the
			// interpreter's hash seed, which is a flaky test wearing a
			// differential's clothes. Sweeping the seed and requiring
			// containment turns the doc comment's claim into the assertion.
			if intersects(got.Promoted, got.Retired) {
				assertAmongCPythonAnswers(t, c.rows, c.stats, c.dryRun,
					c.minUses, readFile(t, skillsPath(goWS)))
			} else {
				cmpJSONL(t, "skills.jsonl", readFile(t, skillsPath(goWS)), want.Skills)
			}
			cmpJSONL(t, "skills_archive.jsonl",
				readFile(t, skillsArchivePath(goWS)), want.Archive)

			gotProv := provenanceNames(t, goWS)
			if !sameSet(gotProv, want.Provenance) {
				t.Errorf("provenance sidecars = %v, CPython wrote %v",
					sortedCopy(gotProv), want.Provenance)
			}
		})
	}
}

// withField overrides one key on a fixture row, returning a COPY: the
// builders above are called several times per table and a shared map would
// leak an override into the next case.
func withField(m map[string]any, k string, v any) map[string]any {
	out := map[string]any{}
	for key, val := range m {
		out[key] = val
	}
	out[k] = v
	return out
}

func selfVariant(id string) map[string]any {
	return map[string]any{"id": id, "name": "self-" + id,
		"description": "d", "steps_template": []any{"s"},
		"variant_of": id, "variant_wins": 9, "variant_losses": 1,
		"utility_score": 0.1, "use_count": 10,
		"created_at": "2026-01-03T00:00:00+00:00"}
}

func seedStore(t *testing.T, ws string, rows, stats []map[string]any) {
	t.Helper()
	write := func(path string, rs []map[string]any, stampHash bool) {
		if rs == nil {
			return
		}
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		var buf []byte
		for _, r := range rs {
			if stampHash {
				row := map[string]any{}
				for k, v := range r {
					row[k] = v
				}
				row["content_hash"] = ComputeSkillHash(skillOf(t, r))
				r = row
			}
			b, err := json.Marshal(r)
			if err != nil {
				t.Fatal(err)
			}
			buf = append(buf, b...)
			buf = append(buf, '\n')
		}
		if err := os.WriteFile(path, buf, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write(skillsPath(ws), rows, true)
	write(skillStatsPath(ws), stats, false)
}

func readFile(t *testing.T, p string) string {
	t.Helper()
	b, err := os.ReadFile(p)
	if err != nil {
		if os.IsNotExist(err) {
			return ""
		}
		t.Fatal(err)
	}
	return string(b)
}

// cmpJSONL compares two JSONL files as multisets of decoded rows. Row order
// is each runtime's rewrite order and is not a contract; the rows are.
// intersects reports whether any id appears in both lists — for the retire
// report, the structural signature of a variant CHAIN whose links both won,
// which is the only shape whose stored result depends on group order.
func intersects(a, b []string) bool {
	in := map[string]bool{}
	for _, s := range a {
		in[s] = true
	}
	for _, s := range b {
		if in[s] {
			return true
		}
	}
	return false
}

// hashSeeds are the PYTHONHASHSEED values the sweep runs under. Twelve, not
// two: the order a set yields two strings in is a property of their hashes
// modulo the table size, so a given pair can favour one order heavily.
// Measured for the ids this file's chain fixture uses, list({"p1","c1"}) came
// out ['p1','c1'] under 8 of these seeds and ['c1','p1'] under the other 4 —
// a two-seed sweep would have had a one-in-nine chance of seeing only one
// answer and concluding the case was unambiguous.
var hashSeeds = []string{"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"}

// assertAmongCPythonAnswers runs the CPython probe once per hash seed and
// requires the port's skills.jsonl to match one of the distinct answers it
// gets back.
//
// This is a WEAKER assertion than equality and it is deliberately confined
// to the fixtures that earn it: RetireLosingVariants' doc comment claims the
// port's first-appearance order is "inside the set of orders CPython can
// produce", and a claim in a comment is exactly the kind that decays (lens
// 19). Running it makes the claim falsifiable — if the port ever picks an
// order CPython cannot reach, this fails, where comparing against one
// arbitrary seed would just flake.
//
// Each seed gets a FRESH workspace. The probe seeds skills.jsonl with "w",
// but it APPENDS to the archive and writes new provenance sidecars, so
// reusing one directory would accumulate every earlier seed's leavings and
// the answers would differ for a reason that has nothing to do with order.
func assertAmongCPythonAnswers(t *testing.T, rows, stats []map[string]any,
	dryRun bool, minUses int, goSkills string) {
	t.Helper()
	arg := map[string]any{"rows": rows, "stats": stats,
		"dry_run": dryRun, "min_uses": minUses}

	distinct := map[string]bool{}
	var samples []string
	for _, seed := range hashSeeds {
		var want struct {
			OK     bool   `json:"ok"`
			Skills string `json:"skills"`
			Loaded int    `json:"loaded"`
			Cls    string `json:"cls"`
			Msg    string `json:"msg"`
		}
		pyprobe.Probe{Marker: "skills.py", Workspace: t.TempDir(),
			Env: []string{"PYTHONHASHSEED=" + seed}}.RunJSON(
			t, pyVariantSrc, &want, pyprobe.Arg(t, arg))
		if !want.OK {
			t.Fatalf("CPython raised under PYTHONHASHSEED=%s: %s: %s",
				seed, want.Cls, want.Msg)
		}
		if want.Loaded != len(rows) {
			t.Fatalf("CPython loaded %d of %d seeded rows under seed %s",
				want.Loaded, len(rows), seed)
		}
		key := strings.Join(normJSONL(t, want.Skills), "\n")
		if !distinct[key] {
			distinct[key] = true
			samples = append(samples, key)
		}
	}

	// Chain-shaped is NECESSARY for ambiguity, not sufficient: a chain whose
	// two challengers carry the SAME content produces one file either way.
	// The existing "a promoted parent that is itself retired" fixture is
	// exactly that — it routes here on shape and the sweep answers once.
	//
	// When CPython has one answer, assert ON it. Containment against a
	// single candidate is equality with the failure message thrown away, and
	// weakening a case that did not need weakening is how a differential
	// stops noticing things. The test is as strong as CPython's own
	// determinism permits, fixture by fixture, decided by measurement rather
	// than by the shape heuristic that got it here.
	got := strings.Join(normJSONL(t, goSkills), "\n")
	if len(distinct) == 1 {
		if got != samples[0] {
			t.Errorf("skills.jsonl: CPython answers identically under all %d "+
				"hash seeds, so this is a STRICT mismatch.\n go:\n%s\n py:\n%s",
				len(hashSeeds), got, samples[0])
		}
		return
	}

	if distinct[got] {
		return
	}
	t.Errorf("the port's skills.jsonl matches NONE of the %d answers CPython "+
		"produced across %d hash seeds — its group order is outside what "+
		"CPython can reach, which the ordering note in variant.go claims it "+
		"is not.\n go:\n%s\n CPython answers:\n%s",
		len(distinct), len(hashSeeds), got, strings.Join(samples, "\n  ---\n"))
}

// normJSONL is the canonical form both the strict comparison and the seed
// sweep compare: rows as re-marshalled JSON, sorted, with a just-minted
// archived_at masked. One implementation, because two would be free to drift
// into disagreeing about what "the same store" means.
func normJSONL(t *testing.T, s string) []string {
	t.Helper()
	var out []string
	for _, line := range splitLines(s) {
		var v any
		if err := json.Unmarshal([]byte(line), &v); err != nil {
			out = append(out, "UNPARSEABLE:"+line)
			continue
		}
		maskFreshArchivedAt(v)
		b, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, string(b))
	}
	sort.Strings(out)
	return out
}

func cmpJSONL(t *testing.T, label, got, want string) {
	t.Helper()
	g, w := normJSONL(t, got), normJSONL(t, want)
	if len(g) != len(w) {
		t.Errorf("%s: %d rows, CPython wrote %d\n go: %v\n py: %v",
			label, len(g), len(w), g, w)
		return
	}
	for i := range g {
		if g[i] != w[i] {
			t.Errorf("%s row %d\n go: %s\n py: %s", label, i, g[i], w[i])
		}
	}
}

// maskFreshArchivedAt blanks an archived_at that the writer minted DURING
// this test, and only that one. The two runtimes are called seconds apart, so
// a live stamp can never agree; but a blanket mask would also excuse a port
// that wrote the wrong timestamp, an empty one, or none at all. The narrow
// rule — parseable AND within two minutes of now — leaves every one of those
// failing, and would leave a SEEDED archived_at (there is none today) having
// to match exactly.
func maskFreshArchivedAt(v any) {
	m, ok := v.(map[string]any)
	if !ok {
		return
	}
	s, ok := m["archived_at"].(string)
	if !ok {
		return
	}
	ts, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		return
	}
	if d := time.Since(ts); d > -2*time.Minute && d < 2*time.Minute {
		m["archived_at"] = "<fresh>"
	}
}

func splitLines(s string) []string {
	var out []string
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			if i > start {
				out = append(out, s[start:i])
			}
			start = i + 1
		}
	}
	if start < len(s) {
		out = append(out, s[start:])
	}
	return out
}

func provenanceNames(t *testing.T, ws string) []string {
	t.Helper()
	dir := filepath.Join(ws, "memory", "skill_provenance")
	entries, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		t.Fatal(err)
	}
	var out []string
	for _, e := range entries {
		n := e.Name()
		if filepath.Ext(n) != ".json" {
			continue
		}
		// The probe reports `name.split("_")[0]`, so the comparison is of
		// the skill-name stem rather than of a microsecond stamp neither
		// runtime can make the other produce.
		cut := len(n)
		for i := 0; i < len(n); i++ {
			if n[i] == '_' {
				cut = i
				break
			}
		}
		out = append(out, n[:cut])
	}
	return out
}

func sameSet(a, b []string) bool {
	x, y := sortedCopy(a), sortedCopy(b)
	if len(x) != len(y) {
		return false
	}
	for i := range x {
		if x[i] != y[i] {
			return false
		}
	}
	return true
}

func sortedCopy(ss []string) []string {
	out := append([]string{}, ss...)
	sort.Strings(out)
	return out
}

// skillOf builds a Skill through the port's own admission path, so the test
// drives production code rather than a hand-filled struct.
func skillOf(t *testing.T, m map[string]any) Skill {
	t.Helper()
	b, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	var o map[string]any
	if err := json.Unmarshal(b, &o); err != nil {
		t.Fatal(err)
	}
	s, serr := DictToSkill(o)
	if serr != nil {
		t.Fatalf("DictToSkill(%v): %v", m, serr)
	}
	return s
}
