package loop

import (
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyLessonTextSrc drives the two observation-form mint helpers.
//
// They are one f-string each and that is exactly why they are pinned: the
// text is a CONTENT KEY. Near-duplicate dedup joins recurring plans on it
// (M3, session 40), so a port that differs by a comma mints a new row every
// run where Python reinforces one — the divergence is invisible in any test
// of the return value's shape and visible only in the store, weeks later.
const pyLessonTextSrc = `
import json, sys
import loop_finalize

_argv = json.loads(sys.argv[1])
print(json.dumps({
    "recovery": loop_finalize._recovery_plan_lesson_text(
        _argv["failure_class"], _argv["action"]),
    "diagnosis": loop_finalize._auto_diagnosis_lesson_text(
        _argv["failure_class"], _argv["recommendation"]),
}, sort_keys=True))
`

func TestLessonTextMatchesCPython(t *testing.T) {
	cases := []struct {
		name           string
		failureClass   string
		action         string
		recommendation string
	}{
		{name: "the ordinary shape", failureClass: "adapter_timeout",
			action:         "retry with a longer deadline",
			recommendation: "raise the per-step timeout"},
		// Empty parts, because the caller reads them out of a diagnosis dict
		// that may not carry them. Python interpolates "" and the sentence
		// keeps both colons; a port that skipped the segment would produce a
		// different key for the same absence.
		{name: "an empty failure class", failureClass: "",
			action: "a", recommendation: "b"},
		{name: "an empty action", failureClass: "c", action: "",
			recommendation: ""},
		// Newlines and separators travel verbatim — no strip anywhere in
		// either helper, so a multi-line action stays multi-line and the key
		// is whatever the advisor wrote.
		{name: "a multi-line action", failureClass: "x",
			action: "one\ntwo\n", recommendation: "\u001fr\u001f"},
		// Percent signs, which are formatting metacharacters in Go and not
		// in an f-string.
		{name: "a percent sign", failureClass: "100%_stall",
			action: "cut the budget by 50%", recommendation: "%s"},
		// Braces, which are metacharacters in an f-string and not in Go.
		{name: "braces", failureClass: "{cls}", action: "{{action}}",
			recommendation: "}{"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			var want struct {
				Recovery  string `json:"recovery"`
				Diagnosis string `json:"diagnosis"`
			}
			pyprobe.Probe{Marker: "loop_finalize.py"}.RunJSON(t, pyLessonTextSrc, &want,
				pyprobe.Arg(t, map[string]any{
					"failure_class": c.failureClass, "action": c.action,
					"recommendation": c.recommendation,
				}))
			if got := RecoveryPlanLessonText(c.failureClass, c.action); got != want.Recovery {
				t.Errorf("recovery\n go: %q\n py: %q", got, want.Recovery)
			}
			if got := AutoDiagnosisLessonText(c.failureClass, c.recommendation); got != want.Diagnosis {
				t.Errorf("diagnosis\n go: %q\n py: %q", got, want.Diagnosis)
			}
		})
	}
}

// pyMaintenanceSrc drives the deferred-maintenance registry through the
// sequences that decide its contract: ordering, the count a failure still
// reports, the per-handle isolation, and what a re-drain returns.
const pyMaintenanceSrc = `
import json, sys
import loop_finalize

_argv = json.loads(sys.argv[1])
_ran = []

def _mk(tag, boom):
    def _fn():
        _ran.append(tag)
        if boom:
            raise RuntimeError("boom " + tag)
    return _fn

# "or []" because a Go nil slice marshals to JSON null, and the
# "unknown handle" fixture registers nothing. The probe must survive the
# empty case it is there to measure.
for reg in (_argv["register"] or []):
    loop_finalize.defer_maintenance_post_notify(
        reg["handle"], _mk(reg["tag"], reg["boom"]))

_counts = [loop_finalize.drain_deferred_maintenance(h) for h in _argv["drain"]]
print(json.dumps({"ran": _ran, "counts": _counts,
                  "left": sorted(loop_finalize._POST_NOTIFY_MAINTENANCE)},
                 sort_keys=True))
`

// TestDeferredMaintenanceMatchesCPython pins the registry's contract, and
// the interesting half is the COUNT.
//
// drain returns len(fns) after a loop that swallows every exception, so a
// drain of three where two raise still answers 3. It is "how much tail work
// this handle had", not "how much of it worked" — and a port that counted
// successes would agree on every fixture where nothing fails, which is every
// fixture anyone writes first.
func TestDeferredMaintenanceMatchesCPython(t *testing.T) {
	type reg struct {
		Handle string `json:"handle"`
		Tag    string `json:"tag"`
		Boom   bool   `json:"boom"`
	}
	cases := []struct {
		name     string
		register []reg
		drain    []string
	}{
		{name: "two in order", drain: []string{"h1"}, register: []reg{
			{Handle: "h1", Tag: "a"}, {Handle: "h1", Tag: "b"},
		}},
		// A failure does not stop the rest, and it does not reduce the count.
		{name: "a raising callable is counted and does not stop the rest",
			drain: []string{"h1"}, register: []reg{
				{Handle: "h1", Tag: "a", Boom: true},
				{Handle: "h1", Tag: "b"},
				{Handle: "h1", Tag: "c", Boom: true},
			}},
		{name: "every callable raising still counts them all",
			drain: []string{"h1"}, register: []reg{
				{Handle: "h1", Tag: "a", Boom: true},
				{Handle: "h1", Tag: "b", Boom: true},
			}},
		// Handles are isolated, and draining one leaves the other pending.
		{name: "handles are isolated", drain: []string{"h1"}, register: []reg{
			{Handle: "h1", Tag: "a"}, {Handle: "h2", Tag: "b"},
		}},
		// A second drain of the same handle is zero — the pop cleared it.
		{name: "a re-drain is zero", drain: []string{"h1", "h1"},
			register: []reg{{Handle: "h1", Tag: "a"}}},
		// An unknown handle is zero, not an error.
		{name: "an unknown handle is zero", drain: []string{"nope"}},
		// The EMPTY handle id is a real key, not a synonym for absent —
		// current_handle_id can answer "" for a run-dir named "-x".
		{name: "the empty handle id is a real key", drain: []string{""},
			register: []reg{{Handle: "", Tag: "a"}}},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			// The Go registry is package state, so it must be emptied
			// between subtests or one case's leftovers become the next
			// one's fixture.
			resetMaintenanceRegistry()
			t.Cleanup(resetMaintenanceRegistry)

			var want struct {
				Ran    []string `json:"ran"`
				Counts []int    `json:"counts"`
				Left   []string `json:"left"`
			}
			pyprobe.Probe{Marker: "loop_finalize.py"}.RunJSON(t, pyMaintenanceSrc, &want,
				pyprobe.Arg(t, map[string]any{
					"register": c.register, "drain": c.drain,
				}))

			var ran []string
			var mu sync.Mutex
			for _, r := range c.register {
				r := r
				DeferMaintenancePostNotify(r.Handle, func() {
					mu.Lock()
					ran = append(ran, r.Tag)
					mu.Unlock()
					if r.Boom {
						panic("boom " + r.Tag)
					}
				})
			}
			var counts []int
			for _, h := range c.drain {
				counts = append(counts, DrainDeferredMaintenance(h))
			}
			if strings.Join(ran, ",") != strings.Join(want.Ran, ",") {
				t.Errorf("ran\n go: %v\n py: %v", ran, want.Ran)
			}
			if len(counts) != len(want.Counts) {
				t.Fatalf("counts go=%v py=%v", counts, want.Counts)
			}
			for i := range counts {
				if counts[i] != want.Counts[i] {
					t.Errorf("drain %d count go=%d py=%d", i, counts[i], want.Counts[i])
				}
			}
			left := pendingMaintenanceHandles()
			if strings.Join(left, ",") != strings.Join(want.Left, ",") {
				t.Errorf("still registered\n go: %v\n py: %v", left, want.Left)
			}
		})
	}
}

// TestADrainDoesNotHoldTheLockAcrossCallables is the doc comment's claim,
// turned into a test that FAILS if it is wrong.
//
// It hung, in its first form. A deadlocked test is worse than a failing one:
// it stalls whatever is running it, and the mutation battery that found the
// bug timed out at ten minutes and left the mutant on disk instead of
// reporting a detection. So the drain runs on its own goroutine behind a
// deadline, and the assertion is about the deadline.
//
// A callable is entitled to register more maintenance — Python cannot
// deadlock doing it, so neither may this. The re-registered work lands in a
// fresh list and is NOT run by the drain in progress, which is also Python's
// behaviour (the pop already took the old list).
func TestADrainDoesNotHoldTheLockAcrossCallables(t *testing.T) {
	resetMaintenanceRegistry()
	t.Cleanup(resetMaintenanceRegistry)

	var order []string
	DeferMaintenancePostNotify("h", func() {
		order = append(order, "first")
		DeferMaintenancePostNotify("h", func() { order = append(order, "second") })
	})
	done := make(chan int, 1)
	go func() { done <- DrainDeferredMaintenance("h") }()
	select {
	case n := <-done:
		if n != 1 {
			t.Errorf("first drain ran %d, want 1 — the re-registered "+
				"callable must not join the drain in progress", n)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("the drain did not return within 5s — it is holding the " +
			"registry lock across the callables, and a callable that " +
			"registers more maintenance deadlocks it. CPython cannot " +
			"deadlock here, so this port must not either.")
	}
	if n := DrainDeferredMaintenance("h"); n != 1 {
		t.Errorf("second drain ran %d, want 1", n)
	}
	if strings.Join(order, ",") != "first,second" {
		t.Errorf("order = %v", order)
	}
}

// TestANilCallableIsADrainWarningNotAFinalizePanic pins the direction the
// nil is allowed to fail in: registration accepts it, the drain catches it.
func TestANilCallableIsADrainWarningNotAFinalizePanic(t *testing.T) {
	resetMaintenanceRegistry()
	t.Cleanup(resetMaintenanceRegistry)

	DeferMaintenancePostNotify("h", nil)
	ran := false
	DeferMaintenancePostNotify("h", func() { ran = true })
	if n := DrainDeferredMaintenance("h"); n != 2 {
		t.Errorf("drain counted %d, want 2 — a nil is ATTEMPTED work", n)
	}
	if !ran {
		t.Error("the callable after the nil did not run — the recover must " +
			"be per-callable, not around the loop")
	}
}

func resetMaintenanceRegistry() {
	postNotifyMaintenance.Lock()
	defer postNotifyMaintenance.Unlock()
	postNotifyMaintenance.m = map[string][]func(){}
}

func pendingMaintenanceHandles() []string {
	postNotifyMaintenance.Lock()
	defer postNotifyMaintenance.Unlock()
	out := make([]string, 0, len(postNotifyMaintenance.m))
	for k := range postNotifyMaintenance.m {
		out = append(out, k)
	}
	sortStrings(out)
	return out
}

func sortStrings(xs []string) {
	for i := 1; i < len(xs); i++ {
		for j := i; j > 0 && xs[j] < xs[j-1]; j-- {
			xs[j], xs[j-1] = xs[j-1], xs[j]
		}
	}
}
