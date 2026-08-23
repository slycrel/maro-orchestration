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
	// The rotation audit row (r5 L2). "system", checked against the live
	// frozenset: LOG_ROTATED is in Python's EVENT_TYPES and not in its
	// USER_SURFACED_EVENTS.
	"LOG_ROTATED": "system",
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
	// A literal-only census is BLIND to an emitter that passes a variable,
	// and there is one: skills.LogCircuitTransition selects its event type
	// from a map, so all three SKILL_CIRCUIT_* types were declared in the
	// registry above and verified by nothing (adversarial r7 MEDIUM).
	//
	// Two mechanisms answer that. indirect finds the non-literal call
	// sites; a file holding one is not trusted to the regex, so instead
	// every event-type-shaped literal ANYWHERE in it is harvested into the
	// census (eventShaped). And any such file that is not named in
	// indirectSites fails outright, so the next variable emitter is a red
	// test rather than another silent hole.
	indirect := regexp.MustCompile(`\.Event(?:Related|Noted)?\(\s*[^"\s]`)
	eventShaped := regexp.MustCompile(`"([A-Z][A-Z0-9_]{2,})"`)
	indirectSites := map[string]string{
		"skills/utility.go": "circuit state -> event type via a map literal",
		"record/record.go":  "Event/EventRelated delegating to EventNoted",
	}
	found := map[string][]string{}
	var unexpectedIndirect []string
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
		rel, _ := filepath.Rel(internalDir, path)
		for _, m := range call.FindAllStringSubmatch(string(src), -1) {
			found[m[1]] = append(found[m[1]], rel)
		}
		if indirect.Match(src) {
			if _, ok := indirectSites[filepath.ToSlash(rel)]; !ok {
				unexpectedIndirect = append(unexpectedIndirect, rel)
				return nil
			}
			for _, m := range eventShaped.FindAllStringSubmatch(string(src), -1) {
				found[m[1]] = append(found[m[1]], rel+" (non-literal emitter)")
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(unexpectedIndirect) > 0 {
		t.Errorf("these files pass a non-literal event type to an Event* "+
			"writer, so the literal census cannot see what they emit: %s\n"+
			"Either pass a literal, or add the file to indirectSites so its "+
			"event-shaped literals are harvested instead.",
			strings.Join(unexpectedIndirect, ", "))
	}
	for site := range indirectSites {
		var seen bool
		for _, sites := range found {
			for _, s := range sites {
				if strings.HasPrefix(filepath.ToSlash(s), site+" ") {
					seen = true
				}
			}
		}
		if !seen {
			t.Errorf("indirectSites names %q, but the walk harvested no "+
				"event type from it — the entry is stale and is masking "+
				"the very blind spot it was added to cover", site)
		}
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
