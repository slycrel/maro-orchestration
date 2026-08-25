package skills

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyAttributeSrc seeds a store, runs attribute_failure_to_skills, and
// reports BOTH halves of what it did: the ids it returned AND the utility
// rows it wrote. A return-value-only comparison would pass for a port that
// attributed the right skills and recorded nothing against them, which is
// the entire point of the function.
const pyAttributeSrc = `
import json, sys
import skills
from skill_types import dict_to_skill

_argv = json.loads(sys.argv[1])

p = skills._skills_path()
p.parent.mkdir(parents=True, exist_ok=True)
with p.open("w", encoding="utf-8") as fh:
    for row in _argv["rows"]:
        row = dict(row)
        row["content_hash"] = skills.compute_skill_hash(dict_to_skill(row))
        fh.write(json.dumps(row) + "\n")

_loaded = len(skills.load_skills())

kw = {}
if _argv["only_ids"] is not None:
    kw["only_ids"] = _argv["only_ids"]

try:
    ids = skills.attribute_failure_to_skills(
        _argv["step_text"], _argv["failure_reason"],
        goal=_argv["goal"], **kw)
    out = {"ok": True, "attributed": ids}
except BaseException as e:
    out = {"ok": False, "cls": type(e).__name__, "msg": str(e)}

out["loaded"] = _loaded
# The SIDE EFFECT. Every skill's failure bookkeeping, keyed by id, so the
# comparison covers what was written and not only what was named.
after = {}
for s in skills.load_skills():
    after[s.id] = {
        "use_count": s.use_count,
        "success_rate": s.success_rate,
        "utility_score": s.utility_score,
        "consecutive_failures": s.consecutive_failures,
        "consecutive_successes": s.consecutive_successes,
        "circuit_state": s.circuit_state,
        "failure_notes": list(s.failure_notes),
    }
out["after"] = after
print(json.dumps(out, sort_keys=True))
`

// TestAttributeFailureToSkillsMatchesCPython pins attribution against the
// interpreter, on the axis the function actually turns on.
//
// The table is built from the FUNCTION: the three states of only_ids (a
// port with two would silently score the whole library for a run with an
// empty manifest), the unconditional " " separator between step and goal,
// and the difference between "matched" and "attributed".
func TestAttributeFailureToSkillsMatchesCPython(t *testing.T) {
	skill := func(id, name string, triggers ...string) map[string]any {
		tp := make([]any, len(triggers))
		for i, s := range triggers {
			tp[i] = s
		}
		return map[string]any{"id": id, "name": name,
			"description": "d " + id, "steps_template": []any{"s1"},
			"trigger_patterns": tp, "tier": "established",
			"created_at": "2026-01-01T00:00:00+00:00"}
	}

	for _, c := range []struct {
		name     string
		rows     []map[string]any
		stepText string
		goal     string
		reason   string
		// onlyIDs nil with restrict false is Python's `only_ids=None`;
		// non-nil (including empty) is the restricted mode.
		onlyIDs  []string
		restrict bool
	}{
		{"nothing matches", []map[string]any{
			skill("s1", "deploy", "deploy the service")},
			"write a poem about cats", "", "boom", nil, false},

		{"one skill matches and is attributed", []map[string]any{
			skill("s1", "deploy", "deploy the service"),
			skill("s2", "poem", "write a poem")},
			"deploy the service to prod", "", "connection refused", nil, false},

		{"two skills match", []map[string]any{
			skill("s1", "deploy", "deploy the service"),
			skill("s2", "deploy2", "deploy the service")},
			"deploy the service to prod", "", "boom", nil, false},

		// The GOAL is concatenated onto the step text, so a trigger that
		// only appears in the goal still matches. A port that scored the
		// step alone passes every fixture above and fails this one.
		{"the goal contributes to the match", []map[string]any{
			skill("s1", "deploy", "deploy the service")},
			"run the thing", "deploy the service", "boom", nil, false},
		// ...and the SEPARATOR between them is load-bearing, which no
		// fixture above can show. The keyword tier is SUBSTRING matching in
		// both directions, so losing one space rarely changes an answer —
		// `rundeploy` still contains `deploy`. It changes this one, because
		// the trigger phrase SPANS the boundary: "run deploy" is a
		// substring of `run` + " " + `deploy` and of neither direction of
		// `rundeploy`, and the TF-IDF fallback finds nothing either, since
		// the joined query tokenizes to the single unknown word
		// "rundeploy". Dropping the space was a MISS against a table of
		// eleven cases until this row existed — lens 11, a separator with
		// no case at its own boundary.
		{"the separator is what a boundary-spanning trigger needs",
			[]map[string]any{skill("s1", "zzz", "run deploy")},
			"run", "deploy", "boom", nil, false},

		// only_ids=None — the legacy full-pool match.
		{"only_ids None scores the whole pool", []map[string]any{
			skill("s1", "deploy", "deploy the service"),
			skill("s2", "deploy2", "deploy the service")},
			"deploy the service", "", "boom", nil, false},
		// only_ids naming one of them.
		{"only_ids restricts to the manifest", []map[string]any{
			skill("s1", "deploy", "deploy the service"),
			skill("s2", "deploy2", "deploy the service")},
			"deploy the service", "", "boom", []string{"s1"}, true},
		// only_ids=[] — restricts to NOTHING. This is the row that
		// separates a three-state port from a two-state one: read as
		// "no manifest", it would attribute both skills and write a
		// failure against each.
		{"an EMPTY only_ids attributes nothing", []map[string]any{
			skill("s1", "deploy", "deploy the service"),
			skill("s2", "deploy2", "deploy the service")},
			"deploy the service", "", "boom", []string{}, true},
		// ...and naming an id that is not in the pool at all.
		{"only_ids naming an absent id", []map[string]any{
			skill("s1", "deploy", "deploy the service")},
			"deploy the service", "", "boom", []string{"nope"}, true},

		// An empty failure reason still records the failure — the note is
		// what changes, not whether the counter moves.
		{"an empty failure reason", []map[string]any{
			skill("s1", "deploy", "deploy the service")},
			"deploy the service", "", "", nil, false},
		// An empty step text with a goal that matches: the separator means
		// the scored string starts with a space.
		{"an empty step text", []map[string]any{
			skill("s1", "deploy", "deploy the service")},
			"", "deploy the service", "boom", nil, false},
		// Both empty — the scored string is exactly " ".
		{"both empty", []map[string]any{
			skill("s1", "deploy", "deploy the service")},
			"", "", "boom", nil, false},

		// A skill already at the circuit's edge, so the attribution CHANGES
		// its state rather than only incrementing a counter. Without this
		// the side-effect comparison only ever sees fresh rows.
		{"a failure that trips the circuit", []map[string]any{
			func() map[string]any {
				m := skill("s1", "deploy", "deploy the service")
				m["consecutive_failures"] = 2
				m["use_count"] = 10
				m["success_rate"] = 0.2
				return m
			}()},
			"deploy the service", "", "again", nil, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()

			var oid any // JSON null for None, a list otherwise
			if c.restrict {
				oid = c.onlyIDs
				if c.onlyIDs == nil {
					oid = []string{}
				}
			}
			arg := map[string]any{"rows": c.rows, "step_text": c.stepText,
				"goal": c.goal, "failure_reason": c.reason, "only_ids": oid}

			var want struct {
				OK         bool            `json:"ok"`
				Attributed []string        `json:"attributed"`
				Loaded     int             `json:"loaded"`
				After      json.RawMessage `json:"after"`
				Cls        string          `json:"cls"`
				Msg        string          `json:"msg"`
			}
			pyprobe.Probe{
				Marker:    "skills.py",
				Workspace: pyWS,
			}.RunJSON(t, pyAttributeSrc, &want, pyprobe.Arg(t, arg))

			if !want.OK {
				t.Fatalf("CPython raised %s: %s — this table's fixtures are "+
					"meant to be well-formed", want.Cls, want.Msg)
			}
			// Anti-vacuity: an inadmissible fixture is skipped by
			// load_skills, and an empty pool attributes nothing while
			// agreeing perfectly.
			if want.Loaded != len(c.rows) {
				t.Fatalf("CPython loaded %d of %d seeded rows; the fixture is "+
					"not admissible", want.Loaded, len(c.rows))
			}

			seedStore(t, goWS, c.rows, nil)
			if n := len(LoadSkills(goWS).Skills); n != len(c.rows) {
				t.Fatalf("the port loaded %d of %d seeded rows", n, len(c.rows))
			}

			got := AttributeFailureToSkills(goWS, c.stepText, c.reason,
				c.goal, c.onlyIDs, c.restrict)

			if !sameSet(got, want.Attributed) {
				t.Errorf("attributed = %v, CPython says %v",
					sortedCopy(got), want.Attributed)
			}

			// The SIDE EFFECT, which is the half a return-value comparison
			// cannot see: a port that named the right ids and wrote nothing
			// would pass every assertion above.
			gotAfter := map[string]map[string]any{}
			for _, s := range LoadSkills(goWS).Skills {
				notes := s.FailureNotes
				if notes == nil {
					notes = []string{}
				}
				gotAfter[s.ID] = map[string]any{
					"use_count":             s.UseCount,
					"success_rate":          s.SuccessRate,
					"utility_score":         s.UtilityScore,
					"consecutive_failures":  s.ConsecutiveFailures,
					"consecutive_successes": s.ConsecutiveSuccesses,
					"circuit_state":         s.CircuitState,
					"failure_notes":         notes,
				}
			}
			gotJSON, err := json.Marshal(gotAfter)
			if err != nil {
				t.Fatal(err)
			}
			if !sameJSON(t, gotJSON, want.After) {
				t.Errorf("the utility rows are not CPython's:\n go: %s\n py: %s",
					gotJSON, want.After)
			}
		})
	}
}

// TestAttributionSkipsASkillWhoseUpdateFails pins the split between
// "matched" and "attributed".
//
// Python appends the id only after update_skill_utility RETURNS, so a
// skill whose write raises is swallowed by the bare except and is absent
// from the answer. That branch has no CPython fixture here — provoking a
// write failure in the interpreter means breaking its store underneath it,
// which the differential harness cannot do without also breaking the read
// it uses to check the result. So it is pinned directly instead, and named
// as the port-only pin it is.
func TestAttributionSkipsASkillWhoseUpdateFails(t *testing.T) {
	ws := t.TempDir()
	rows := []map[string]any{
		{"id": "s1", "name": "deploy", "description": "d",
			"steps_template": []any{"s"}, "trigger_patterns": []any{"deploy it"},
			"tier": "established", "created_at": "2026-01-01T00:00:00+00:00"},
	}
	seedStore(t, ws, rows, nil)
	if got := AttributeFailureToSkills(ws, "deploy it", "boom", "", nil, false); len(got) != 1 {
		t.Fatalf("baseline: attributed %v, want the one matching skill", got)
	}

	// Make the store unwritable, so the update fails while the match still
	// succeeds. A port that appended before the write would still report
	// the id.
	//
	// The DIRECTORY, not the file. Every writer here is an atomic write —
	// temp file plus rename — and a rename needs the directory writable,
	// not the destination. The first cut of this pin chmod'ed skills.jsonl
	// to 0444, the write sailed through, and the pin reported a port bug
	// that was its own. Lens 14: the measurement was evidence about a call
	// that never happened.
	if os.Geteuid() == 0 {
		t.Skip("running as root: permission bits do not restrain the writer")
	}
	seedStore(t, ws, rows, nil)
	dir := filepath.Dir(skillsPath(ws))
	if err := os.Chmod(dir, 0o555); err != nil {
		t.Skipf("cannot chmod the store dir on this filesystem: %v", err)
	}
	t.Cleanup(func() { _ = os.Chmod(dir, 0o755) })

	got := AttributeFailureToSkills(ws, "deploy it", "boom", "", nil, false)
	if len(got) != 0 {
		t.Errorf("attributed %v after the write failed; Python appends only "+
			"after update_skill_utility returns, so a failed write leaves the "+
			"id out of the answer", got)
	}
}
