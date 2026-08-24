package director

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

const pyCadenceSrc = `
import json, os, sys
import director

# _checkin_jitter DRAWS, so the value is useless for comparison — the
# bounds it draws between are the thing under test. Capture them instead of
# the sample, by intercepting randint.
import random
_seen = {}
_real = random.randint
def _spy(lo, hi):
    _seen["lo"], _seen["hi"] = lo, hi
    return lo
random.randint = _spy

# Each helper is asked SEPARATELY, and a raise is one of its answers.
# Python's except tuple here is (TypeError, ValueError) — so a YAML .inf
# is an OverflowError that leaves the helper entirely, out through
# _advance_origin_with_checkin, and turns the whole escalation into a
# surface. A probe that died on it could not compare that at all; a probe
# that asked for both in one expression would let the first raise hide the
# second.
def _try(fn):
    try:
        return {"ok": True, "value": fn()}
    except BaseException as e:
        return {"ok": False, "cls": type(e).__name__, "msg": str(e)}

first = _try(director._checkin_first_depth)
jit = _try(director._checkin_jitter)
random.randint = _real

sys.stdout.write(json.dumps({
    "first": first,
    "jitter": jit,
    "lo": _seen.get("lo"), "hi": _seen.get("hi"),
}))
`

// TestTheCheckinCadenceReadsConfigTheWayPythonDoes drives the two cadence
// helpers through YAML an operator could actually write.
//
// Nothing exercised these against real config: the unit tests build Go
// native maps, where a typed lookup and an int() agree by construction, and
// the differential monkey-patches _checkin_jitter away entirely. Both
// helpers were typed lookups, so a quoted `"3"` was the default 2 here and
// 3 there — and this function decides whether an operator is notified at
// all (adversarial r11 round 2, MEDIUM).
//
// The jitter is compared by its BOUNDS rather than its draw, because a
// differential over a random sample is a differential over a random sample.
func TestTheCheckinCadenceReadsConfigTheWayPythonDoes(t *testing.T) {
	for _, c := range []struct {
		name string
		yaml string
	}{
		{"nothing configured", ""},
		{"plain integers", "recursion:\n  checkin_first_depth: 3\n  checkin_jitter_min: 2\n  checkin_jitter_max: 9\n"},
		// The one that started this: YAML quotes are easy to leave in.
		{"quoted integers", "recursion:\n  checkin_first_depth: \"3\"\n  checkin_jitter_min: \"2\"\n  checkin_jitter_max: \"9\"\n"},
		{"floats", "recursion:\n  checkin_first_depth: 3.7\n  checkin_jitter_min: 2.9\n  checkin_jitter_max: 9.1\n"},
		{"booleans", "recursion:\n  checkin_first_depth: true\n"},
		// ONE bad key resets BOTH ends of the jitter in Python, because the
		// two reads share a try. Two independent defaults draw 7..10 here.
		{"a good min and a garbage max", "recursion:\n  checkin_jitter_min: 10\n  checkin_jitter_max: abc\n"},
		{"a garbage min and a good max", "recursion:\n  checkin_jitter_min: abc\n  checkin_jitter_max: 10\n"},
		// The MISSING CELL between the two rows above and the .inf row
		// below: a CAUGHT min beside an UNCAUGHT max. One try wraps both
		// reads, so `lo` raising jumps straight to the except and `hi` is
		// never evaluated at all — the OverflowError that would have
		// escaped never happens, and CPython draws from 4-7. Evaluating
		// both reads first let it out, which flips the escalation to
		// `surface` and kills the chain. A fixture split across two rows
		// is a fixture that covers neither.
		{"a caught min beside an uncaught max",
			"recursion:\n  checkin_jitter_min: abc\n  checkin_jitter_max: .inf\n"},
		{"a caught null min beside an uncaught max",
			"recursion:\n  checkin_jitter_min: ~\n  checkin_jitter_max: .inf\n"},
		// And the ORDER, from the other side: an uncaught MIN still
		// escapes, however good the max is. With only the row above, a
		// port that skipped the max read unconditionally would pass.
		{"an uncaught min beside a good max",
			"recursion:\n  checkin_jitter_min: .inf\n  checkin_jitter_max: 10\n"},
		{"garbage first depth", "recursion:\n  checkin_first_depth: soon\n"},
		{"null everywhere", "recursion:\n  checkin_first_depth: ~\n  checkin_jitter_min: ~\n  checkin_jitter_max: ~\n"},
		{"a below-floor first depth", "recursion:\n  checkin_first_depth: 0\n"},
		{"an inverted jitter pair", "recursion:\n  checkin_jitter_min: 9\n  checkin_jitter_max: 2\n"},
		{"a below-floor jitter pair", "recursion:\n  checkin_jitter_min: 0\n  checkin_jitter_max: 0\n"},
		// YAML has `.inf` and `.nan`, and they are the two halves of the
		// except tuple: int(nan) is a ValueError and defaults, int(inf) is
		// an OverflowError and does NOT. A config one character apart
		// either keeps the cadence running or takes the escalation lane's
		// continue branch out to a surface, and nothing here could tell
		// them apart while both helpers returned a bare int.
		{"an infinite first depth", "recursion:\n  checkin_first_depth: .inf\n"},
		{"a NaN first depth", "recursion:\n  checkin_first_depth: .nan\n"},
		{"an infinite jitter max", "recursion:\n  checkin_jitter_min: 2\n  checkin_jitter_max: .inf\n"},
		{"an infinite jitter min", "recursion:\n  checkin_jitter_min: .inf\n  checkin_jitter_max: 9\n"},
		{"a NaN jitter max", "recursion:\n  checkin_jitter_min: 2\n  checkin_jitter_max: .nan\n"},
	} {
		t.Run(c.name, func(t *testing.T) {
			ws := t.TempDir()
			if c.yaml != "" {
				if err := os.WriteFile(filepath.Join(ws, "config.yml"),
					[]byte(c.yaml), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			raw := pyprobe.Probe{
				Marker:    "director.py",
				Workspace: ws,
				Guard:     cadenceGuard,
			}.Run(t, pyCadenceSrc)
			var want struct {
				First  pyAnswer `json:"first"`
				Jitter pyAnswer `json:"jitter"`
				Lo     *int     `json:"lo"`
				Hi     *int     `json:"hi"`
			}
			if err := json.Unmarshal([]byte(raw), &want); err != nil {
				t.Fatalf("decoding the probe output: %v\nraw: %s", err, raw)
			}

			cfg, _ := config.LoadFor(ws)
			gotFirst, firstErr := CheckinFirstDepth(cfg)
			want.First.check(t, "first depth", firstErr, gotFirst)

			var gotLo, gotHi int
			drew := false
			prev := checkinRandInt
			checkinRandInt = func(lo, hi int) int {
				gotLo, gotHi, drew = lo, hi, true
				return lo
			}
			gotJit, jitErr := CheckinJitter(cfg)
			checkinRandInt = prev
			want.Jitter.check(t, "jitter", jitErr, gotJit)
			if want.Jitter.OK != drew {
				t.Errorf("the jitter drew = %v, CPython drew = %v",
					drew, want.Jitter.OK)
			}
			if want.Jitter.OK && want.Lo != nil && want.Hi != nil &&
				(gotLo != *want.Lo || gotHi != *want.Hi) {
				t.Errorf("jitter draws from %d..%d, CPython from %d..%d",
					gotLo, gotHi, *want.Lo, *want.Hi)
			}
		})
	}
}

// The resolver under test is config.get, so that is what gets asserted.
const cadenceGuard = `
import os as _o
from config import workspace_root as _wr
_r = str(_wr())
if not _r.startswith(_o.path.realpath(_o.environ["MARO_WORKSPACE"])) and \
   not _r.startswith(_o.environ["MARO_WORKSPACE"]):
    raise SystemExit("refusing to run: workspace_root() resolved to %s" % _r)
`

// pyAnswer is one helper's outcome as CPython reports it: a value, or the
// exception CLASS it raised. The class is the comparison — the two YAML
// spellings that fail here fail into different except tuples upstream.
type pyAnswer struct {
	OK    bool   `json:"ok"`
	Value *int   `json:"value"`
	Cls   string `json:"cls"`
	Msg   string `json:"msg"`
}

func (a pyAnswer) check(t *testing.T, what string, err error, got int) {
	t.Helper()
	if (err != nil) != !a.OK {
		if err != nil {
			// a.Value is a *int, and %v of a pointer is an ADDRESS — the
			// message read "CPython answered 0xc0002105a8" on every
			// failure this assertion has ever produced.
			shown := "no value"
			if a.Value != nil {
				shown = strconv.Itoa(*a.Value)
			}
			t.Fatalf("%s raised %v; CPython answered %s", what, err, shown)
		}
		t.Fatalf("%s answered %d; CPython raises %s: %s", what, got, a.Cls, a.Msg)
	}
	if !a.OK {
		if cls := pyval.ClassOf(err); cls == "" {
			t.Errorf("%s raised %v, which carries no exception class; "+
				"CPython raises %s", what, err, a.Cls)
		} else if cls != a.Cls {
			t.Errorf("%s raises %s, CPython raises %s — the two are caught "+
				"by different excepts upstream", what, pyval.ClassOf(err), a.Cls)
		}
		return
	}
	if a.Value != nil && got != *a.Value {
		t.Errorf("%s = %d, CPython %d", what, got, *a.Value)
	}
}
