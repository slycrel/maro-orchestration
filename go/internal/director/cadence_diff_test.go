package director

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
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
director._checkin_jitter()
random.randint = _real

sys.stdout.write(json.dumps({
    "first_depth": director._checkin_first_depth(),
    "lo": _seen["lo"], "hi": _seen["hi"],
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
		{"garbage first depth", "recursion:\n  checkin_first_depth: soon\n"},
		{"null everywhere", "recursion:\n  checkin_first_depth: ~\n  checkin_jitter_min: ~\n  checkin_jitter_max: ~\n"},
		{"a below-floor first depth", "recursion:\n  checkin_first_depth: 0\n"},
		{"an inverted jitter pair", "recursion:\n  checkin_jitter_min: 9\n  checkin_jitter_max: 2\n"},
		{"a below-floor jitter pair", "recursion:\n  checkin_jitter_min: 0\n  checkin_jitter_max: 0\n"},
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
				FirstDepth int `json:"first_depth"`
				Lo         int `json:"lo"`
				Hi         int `json:"hi"`
			}
			if err := json.Unmarshal([]byte(raw), &want); err != nil {
				t.Fatalf("decoding the probe output: %v\nraw: %s", err, raw)
			}

			cfg, _ := config.LoadFor(ws)
			if got := CheckinFirstDepth(cfg); got != want.FirstDepth {
				t.Errorf("first depth = %d, CPython %d", got, want.FirstDepth)
			}
			var gotLo, gotHi int
			prev := checkinRandInt
			checkinRandInt = func(lo, hi int) int { gotLo, gotHi = lo, hi; return lo }
			CheckinJitter(cfg)
			checkinRandInt = prev
			if gotLo != want.Lo || gotHi != want.Hi {
				t.Errorf("jitter draws from %d..%d, CPython from %d..%d",
					gotLo, gotHi, want.Lo, want.Hi)
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
