package orch

import (
	"encoding/json"
	"os/exec"
	"testing"
)

// _is_chain_shaped decides the EXECUTION LANE — sequential main-thread
// walk vs the DAG scheduler, with different failure and context
// semantics. Every other pin on it goes through DecomposeMission, whose
// ids come from a generator and are never blank, so nothing could see
// the sentinel collision: Python's `prev_id = None` is a third value that
// Go's `prevID == ""` is not, and LoadMission accepts `"id": ""`.
//
// A mutant that reinstated the "" sentinel survived the whole suite
// before this file existed.

const pyChainSnippet = `
import json, sys, mission

out = []
for spec in json.loads(sys.argv[1]):
    ms = [mission.Milestone(id=m['id'], title='t', features=[],
                            validation_criteria=[], status='pending',
                            depends_on=list(m['depends_on'] or [])) for m in spec]
    m = mission.Mission(id='x', goal='g', project='p', milestones=ms,
                        status='pending', created_at='t')
    out.append(mission._is_chain_shaped(m))
print(json.dumps(out))
`

type chainMS struct {
	ID        string   `json:"id"`
	DependsOn []string `json:"depends_on"`
}

// chainCorpus is a package var so the anti-vacuity guard reads the same
// list the differential does.
var chainCorpus = []struct {
	name string
	ms   []chainMS
}{
	{"the empty mission is trivially a chain", []chainMS{}},
	{"one root milestone", []chainMS{{"a", nil}}},
	{"a real chain", []chainMS{{"a", nil}, {"b", []string{"a"}}, {"c", []string{"b"}}}},
	{"a first milestone with a dependency is not a chain",
		[]chainMS{{"a", []string{"z"}}, {"b", []string{"a"}}}},
	{"a skipped edge is not a chain",
		[]chainMS{{"a", nil}, {"b", nil}}},
	{"a dependency on the WRONG predecessor is not a chain",
		[]chainMS{{"a", nil}, {"b", []string{"a"}}, {"c", []string{"a"}}}},
	{"TWO dependencies is not a chain",
		[]chainMS{{"a", nil}, {"b", []string{"a", "a"}}}},

	// The sentinel collision. `prev_id = None` is a value no milestone id
	// can equal; "" is not.
	{"a blank FIRST id still requires the second to name it",
		[]chainMS{{"", nil}, {"b", []string{""}}}},
	{"a blank first id does NOT excuse a missing edge",
		[]chainMS{{"", nil}, {"b", nil}}},
	{"two blank ids in a row",
		[]chainMS{{"", nil}, {"", []string{""}}}},
	{"a blank id in the MIDDLE of a chain",
		[]chainMS{{"a", nil}, {"", []string{"a"}}, {"c", []string{""}}}},
	{"a blank id followed by a milestone depending on nothing",
		[]chainMS{{"a", nil}, {"", []string{"a"}}, {"c", nil}}},
}

func pyChain(t *testing.T, corpus []struct {
	name string
	ms   []chainMS
}) []bool {
	t.Helper()
	specs := make([][]chainMS, 0, len(corpus))
	for _, tc := range corpus {
		specs = append(specs, tc.ms)
	}
	b, err := json.Marshal(specs)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", pyChainSnippet, string(b))
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDirOrch(t))
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("the CPython probe FAILED (exit %d):\n%s",
				ee.ExitCode(), ee.Stderr)
		}
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("python3 is present but the probe could not run: %v", err)
	}
	var got []bool
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("decoding CPython output: %v\nraw: %s", err, out)
	}
	return got
}

func TestIsChainShapedMatchesCPython(t *testing.T) {
	want := pyChain(t, chainCorpus)
	if len(want) != len(chainCorpus) {
		t.Fatalf("CPython returned %d verdicts for %d cases",
			len(want), len(chainCorpus))
	}
	for i, tc := range chainCorpus {
		t.Run(tc.name, func(t *testing.T) {
			m := &Mission{}
			for _, x := range tc.ms {
				m.Milestones = append(m.Milestones,
					Milestone{ID: x.ID, DependsOn: x.DependsOn})
			}
			if got := IsChainShaped(m); got != want[i] {
				t.Fatalf("verdict: go %v, py %v", got, want[i])
			}
		})
	}
}

// Both lanes have to be reachable, or the corpus passes against
// `return true`.
func TestTheChainCorpusReachesBothLanes(t *testing.T) {
	yes, no := 0, 0
	for _, v := range pyChain(t, chainCorpus) {
		if v {
			yes++
		} else {
			no++
		}
	}
	if yes == 0 || no == 0 {
		t.Fatalf("CPython reached one lane only (chain=%d dag=%d)", yes, no)
	}
}
