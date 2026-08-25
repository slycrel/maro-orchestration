package loop

import (
	"fmt"
	"os"
	"sync"
)

// RecoveryPlanLessonText is loop_finalize._recovery_plan_lesson_text — the
// observation-form mint text for an advisor-proposed recovery plan.
//
// What-not-how (2026-08-02, surprise-read M9): the old form minted the
// advisor's procedure bare, so the row read as an INSTRUCTION the next run
// obeys. This form states what happened (the diagnosis) and marks the plan
// as an unverified proposal, so the planner weighs it as evidence.
//
// Deterministic per (failureClass, action) on purpose — recurring plans
// reinforce through near-duplicate dedup (M3, session 40) rather than
// through a timestamp that makes every mint unique. That is why there is no
// clock in here, and why adding one would silently disable the dedup.
func RecoveryPlanLessonText(failureClass, action string) string {
	return fmt.Sprintf("[recovery-plan] %s: this failure class was "+
		"diagnosed on a run; advisor-proposed recovery (unverified): %s",
		failureClass, action)
}

// AutoDiagnosisLessonText is loop_finalize._auto_diagnosis_lesson_text — the
// observation-form mint text for a deterministic loop diagnosis.
func AutoDiagnosisLessonText(failureClass, recommendation string) string {
	return fmt.Sprintf("[auto-diagnosis] %s: diagnosed on a run; "+
		"classifier recommendation (unverified): %s",
		failureClass, recommendation)
}

// The post-notify maintenance registry (async-tail decree).
//
// # Why Python puts it in this module, and why the port keeps it here
//
// Python's comment is a module-identity story: `handle.py` is also a
// documented `python -m handle` entry point, so run that way the module
// executes as `__main__` while an `import handle` elsewhere loads a SECOND
// copy with its own registry. The registrar and the drainer would then hold
// different dicts and every deferred callable would strand undrained (3-lens
// review of 707a541, Architect HIGH). `loop_finalize` is only ever imported
// by its canonical name, so both sides share one registry.
//
// Go has no such hazard — a package is initialised once per binary, and
// there is no `__main__` twin. So the REASON evaporates and the PLACEMENT
// stays: putting it somewhere else would be a port that silently disagrees
// with its source about where a piece of state lives, for a benefit nobody
// asked for.
//
// # What does NOT carry over is the free lock
//
// Python's dict is unsynchronised because the GIL makes `setdefault`,
// `append` and `pop` atomic enough for this use. A Go map is not, and the
// two call sites are on different sides of a notify: registration happens
// during finalize and the drain happens after the operator is told. So the
// map is behind a mutex, which is not a design choice — it is the same
// safety Python was getting for free.
var postNotifyMaintenance = struct {
	sync.Mutex
	m map[string][]func()
}{m: map[string][]func(){}}

// DeferMaintenancePostNotify is defer_maintenance_post_notify — register fn
// to run after handleID's notification goes out.
//
// A nil fn is registered rather than rejected: Python appends whatever it is
// given and finds out at drain time, where the failure is caught and logged.
// Refusing here would move a failure from "one warning in a drain" to "a
// panic in finalize", which is the wrong direction for a maintenance tail.
func DeferMaintenancePostNotify(handleID string, fn func()) {
	postNotifyMaintenance.Lock()
	defer postNotifyMaintenance.Unlock()
	postNotifyMaintenance.m[handleID] = append(postNotifyMaintenance.m[handleID], fn)
}

// DrainDeferredMaintenance is drain_deferred_maintenance — run and clear any
// registered maintenance for handleID, returning how many callables ran.
//
// NEVER raises, and the count is of callables ATTEMPTED, not succeeded:
// Python returns `len(fns)` after a loop whose body swallows every
// exception, so a drain of three where two fail still answers 3. The number
// is "how much tail work this handle had", not "how much of it worked", and
// a caller reading it as the latter would report success it never observed.
//
// The pop happens under the lock and the callables run OUTSIDE it. Holding
// the lock across arbitrary user code would let a callable that registers
// more maintenance deadlock the drain — Python cannot deadlock there, so the
// port must not either. The consequence is the same as Python's: work
// registered for this handle DURING the drain lands in a fresh list and is
// not run by this call.
func DrainDeferredMaintenance(handleID string) int {
	postNotifyMaintenance.Lock()
	fns := postNotifyMaintenance.m[handleID]
	delete(postNotifyMaintenance.m, handleID)
	postNotifyMaintenance.Unlock()

	for _, fn := range fns {
		func() {
			// A panic is Go's spelling of the exception Python catches, and
			// it has to be caught per-callable: recovering around the whole
			// loop would abandon the remaining callables, where Python runs
			// every one of them.
			defer func() {
				if r := recover(); r != nil {
					fmt.Fprintf(os.Stderr,
						"[maro] deferred maintenance failed for handle %s: %v\n",
						handleID, r)
				}
			}()
			if fn == nil {
				// `None()` is a TypeError into the same except.
				panic("deferred maintenance callable is nil")
			}
			fn()
		}()
	}
	return len(fns)
}
