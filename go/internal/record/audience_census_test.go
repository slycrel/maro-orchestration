package record

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// knownEmittedEvents is every event type this runtime writes, with the
// audience Python's USER_SURFACED_EVENTS gives it. It is a CENSUS, not a
// preference: the audience keys on event type in both runtimes, never on
// caller discretion, so a Go row and a Python row about the same decision
// must land in the same lane.
//
// Verified against Python's frozenset (captains_log.py) on 2026-08-23.
// Note SKILL_CIRCUIT_HALF_OPEN's deliberate absence from the user lane:
// half-open is a probation STATE, not a decision, and the trip and the
// recovery that bracket it are both surfaced.
var knownEmittedEvents = map[string]string{
	"SKILL_PROMOTED":          "user",
	"SKILL_DEMOTED":           "user",
	"SKILL_CIRCUIT_OPEN":      "user",
	"SKILL_CIRCUIT_CLOSED":    "user",
	"SKILL_CIRCUIT_HALF_OPEN": "system",
	"ISLAND_CULLED":           "user",
	"EVOLVER_APPLIED":         "user",
	"EVOLVER_REVERTED":        "user",
	"EVOLVER_VERDICT":         "user",
	"EVOLVER_GENERATED":       "system",
	"EVOLVER_SKIPPED":         "system",
	"GRADUATION_PROPOSED":     "user",
	"GRADUATION_VERIFIED":     "user",
	// Bookkeeping and per-step diagnostics. All eight were checked against
	// Python's live frozenset when this census was written and all eight
	// are "system" there — absence is Python's default, so a new event
	// type never leaks into the user lane by accident.
	"LOOP_STARTED":           "system",
	"LOOP_FINISHED":          "system",
	"CLOSURE_VERDICT":        "system",
	"RECALL_PERFORMED":       "system",
	"METACOGNITIVE_DECISION": "system",
	"NOW_ANSWERED":           "system",
	"WORKER_DELEGATION_GAP":  "system",
	"WORKER_REPORT_OMISSION": "system",
	// System in Python too — it is absent from USER_SURFACED_EVENTS there,
	// checked at captains_log.py:412-435. It rides alongside a
	// SKILL_CIRCUIT_OPEN that IS user-surfaced, and that asymmetry is the
	// point: the trip is the decision a user should see, the mismatch is
	// the qualifier an operator finds when they go looking at why.
	"INPUT_MISMATCH": "system",
}

// The registry drifted silently once already: skill slices 3b and 3c
// added five emitters and did not extend userSurfacedEvents, so every
// promotion, demotion, circuit trip and island cull this runtime recorded
// was stamped "system" and dropped from the curated user lane — the
// decision ported faithfully and its announcement did not. A comment
// saying "keep this synced" had been sitting directly above the map the
// whole time, which is why this is a test instead.
//
// It walks the sibling packages' source for event types passed to the
// Event* writers, so a new emitter in a future tranche fails here rather
// than going quietly into the wrong lane.
func TestEveryEmittedEventTypeHasADecidedAudience(t *testing.T) {
	internalDir, err := filepath.Abs("..")
	if err != nil {
		t.Fatal(err)
	}
	call := regexp.MustCompile(`\.Event(?:Related|Noted)?\(\s*"([A-Z][A-Z0-9_]*)"`)
	found := map[string][]string{}
	err = filepath.WalkDir(internalDir, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") ||
			strings.HasSuffix(path, "_test.go") {
			return nil
		}
		src, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		for _, m := range call.FindAllStringSubmatch(string(src), -1) {
			rel, _ := filepath.Rel(internalDir, path)
			found[m[1]] = append(found[m[1]], rel)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(found) == 0 {
		t.Fatal("the walk found no event emitters at all — the pattern has " +
			"stopped matching and this tripwire is dead")
	}
	var undeclared []string
	for evt, sites := range found {
		want, known := knownEmittedEvents[evt]
		if !known {
			undeclared = append(undeclared, evt+" ("+strings.Join(sites, ", ")+")")
			continue
		}
		got := "system"
		if userSurfacedEvents[evt] {
			got = "user"
		}
		if got != want {
			t.Errorf("%s is stamped %q; Python's registry puts it in the %q "+
				"lane (emitted from %s)", evt, got, want, strings.Join(sites, ", "))
		}
	}
	if len(undeclared) > 0 {
		sort.Strings(undeclared)
		t.Errorf("event type(s) emitted with no decided audience — add them to "+
			"knownEmittedEvents AND to userSurfacedEvents if Python's "+
			"USER_SURFACED_EVENTS holds them:\n  %s",
			strings.Join(undeclared, "\n  "))
	}
	// The census must actually reach the skill emitters; if a refactor
	// moves them somewhere this walk cannot see, the test above would pass
	// vacuously.
	for _, must := range []string{"SKILL_PROMOTED", "SKILL_DEMOTED", "ISLAND_CULLED"} {
		if len(found[must]) == 0 {
			t.Errorf("%s was not found by the walk — the census is not "+
				"covering the package that emits it", must)
		}
	}
}
