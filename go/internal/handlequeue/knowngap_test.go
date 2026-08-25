package handlequeue

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// A KNOWN GAP, pinned rather than closed — the convention for an
// accept-not-close residual: the test asserts the DIVERGENT behaviour, so
// whoever closes the gap is told by a failing test which fixtures to
// rewrite, and the gap cannot rot into an unrecorded one.
//
// `pyval.ErrIntTooLarge` is not a CPython exception. It is this port
// refusing a value CPython computes: Python's `int()` is arbitrary
// precision, so `int(10**19)` is simply 10**19, while an int64 cannot hold
// it. Three sites let that refusal become control flow where CPython's
// branch proceeds normally:
//
//   - handlequeue.HandleTask's depth read (handle_queue.py:51). Measured
//     in CPython:
//
//     handle_task routing escalation job_id=j1 depth=10000000000000000000
//
//     and the drain COMPLETES the task. Here HandleTask returns the error,
//     DrainTaskStore's blanket recovery fails the task, and the queue row
//     gets `"status": "failed"` with a Go-invented message in `"error"`.
//
//   - director.CheckinFirstDepth (director.py:1169) and
//     director.CheckinJitter (director.py:1178). There the refusal flips
//     the escalation to `surface`, so no continuation is enqueued at all.
//
// At all three the value is only ever LOGGED or compared, so the abort
// buys nothing — which is what makes this a gap rather than a defensible
// simplification. Closing it means a big-int carrier through pyval, which
// nothing else needs yet; it is named in PORT.md's residuals.
func TestAHugeDepthIsRefusedWhereCPythonComputesIt(t *testing.T) {
	for _, c := range []struct {
		name string
		raw  any
	}{
		{"an integer literal past int64", json.Number("10000000000000000000")},
		{"a float that overflows int64", json.Number("1e19")},
		{"a decimal string past int64", "10000000000000000000"},
	} {
		t.Run(c.name, func(t *testing.T) {
			task := pyval.Obj{
				{Key: "job_id", Val: "j1"},
				{Key: "source", Val: "loop_escalation"},
				{Key: "continuation_depth", Val: c.raw},
			}
			_, err := HandleTask(context.Background(), t.TempDir(), task, Options{})
			if !errors.Is(err, pyval.ErrIntTooLarge) {
				t.Fatalf("HandleTask returned %v; the known gap is that it "+
					"refuses this depth with ErrIntTooLarge. If this now "+
					"succeeds the gap is CLOSED — delete this test and pin "+
					"the agreeing behaviour in the drain differential "+
					"instead", err)
			}
		})
	}
}
