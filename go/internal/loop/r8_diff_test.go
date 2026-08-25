package loop

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// TestSlotHolderMetadataIsJSONDumps: the per-project lock file carries
// the holder's identity, and interrupt.py writes it with
// `path.write_text(json.dumps(payload))`. The GOAL goes in verbatim —
// which is user text, so `>` and non-ASCII are the normal case, not the
// exotic one — and this file is read by whichever runtime contends next.
// A Go-written lock file was therefore telling a Python contender the
// holder's goal in bytes json.dumps never produces
// (adversarial mission-r8).
func TestSlotHolderMetadataIsJSONDumps(t *testing.T) {
	dir := t.TempDir()
	goal := "compare a > b for the café rollout"

	release, warn, err := acquireProjectSlot(dir, "proj", "loop-1", goal)
	if err != nil {
		t.Fatalf("the slot must be free in a fresh dir: %v", err)
	}
	if warn != "" {
		t.Fatalf("no gate warning expected: %s", warn)
	}
	defer release()

	raw, err := os.ReadFile(filepath.Join(dir, "loop-proj.lock"))
	if err != nil {
		t.Fatalf("holder metadata was not written: %v", err)
	}
	line := strings.TrimRight(string(raw), "\x00\n")

	// The payload is interrupt.py's, field for field and in its order.
	// `"started": "` is what this list USED to assert, which is how the
	// port's own wrong key name survived — Python writes "started_at",
	// and observe.py reads exactly that to render a running loop's age.
	// A test can only pin the spelling it was told; this one was told the
	// port's.
	for _, want := range []string{
		`{"loop_id": "loop-1"`, // writer's order + separators
		"\"goal\": \"compare a > b for the caf\\u00e9 rollout\"", // plain >, escaped e
		`"started_at": "`,
		`"project": "proj"`,
	} {
		if !strings.Contains(line, want) {
			t.Errorf("holder metadata is not json.dumps-shaped (missing %s):\n%s",
				want, line)
		}
	}
	// pid is an int in both runtimes and must not have become a float.
	if strings.Contains(line, `"pid": `) && strings.Contains(line, ".0,") {
		t.Errorf("pid must stay an int:\n%s", line)
	}

	// Anti-vacuity: the pre-fix encoder over the same payload, required to
	// lose — and to lose on all three of sorting, HTML escaping and
	// ensure_ascii, so no single fixture property carries the test.
	old, err := json.Marshal(map[string]any{
		"loop_id": "loop-1", "pid": 1, "goal": goal, "started_at": "x",
		"project": "proj",
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, marker := range []string{
		`{"goal":`,     // SORTED: the writer's first key is loop_id
		`a \u003e b`,   // HTML-escaped into six literal chars
		"caf\u00e9 ro", // raw UTF-8, where ensure_ascii writes the escape
	} {
		if !strings.Contains(string(old), marker) {
			t.Fatalf("the pre-fix encoder does not exhibit %s here, so one of "+
				"the forks is untested:\n%s", marker, old)
		}
	}
}

// pySlotPayloadSrc is interrupt.py's lock payload, built the way both of
// its two writers build it (interrupt.py:817-822 and :1095-1100 are the
// same five keys in the same order).
//
// It reconstructs the dict rather than importing the function, and says so:
// _acquire_project_slot needs a live lock path and a real flock, and
// standing that up would put the filesystem between the fixture and the
// four rules under test — the key names, the order, the goal clip, and the
// stamp format.
const pySlotPayloadSrc = `
import json, sys, os
from datetime import datetime, timezone
_argv = json.loads(sys.argv[1])
payload = {
    "loop_id": _argv["loop_id"],
    "goal": _argv["goal"][:120],
    "pid": os.getpid(),
    "started_at": datetime.now(timezone.utc).isoformat(),
    "project": _argv["project"],
}
print(json.dumps({"keys": list(payload), "goal": payload["goal"],
                  "started_at": payload["started_at"]}))
`

// TestSlotPayloadMatchesInterruptPy pins the payload against CPython
// rather than against a hand-written list of substrings.
//
// The substring list above is what the port had, and it encoded the port's
// own key name — so it agreed with the bug for as long as the bug existed.
// This measures instead: the KEYS and their ORDER come from Python, and
// the goal clip is exercised with a goal longer than the limit, which the
// old fixture's 34-character goal could never reach (a limit with no case
// at its own boundary is a limit nothing pins).
func TestSlotPayloadMatchesInterruptPy(t *testing.T) {
	dir := t.TempDir()
	// 200 runes, all multi-byte: the clip is `goal[:120]`, which is 120
	// CODE POINTS in Python. A byte slice would cut this at 120 bytes —
	// 40 characters — and a naive Go []byte slice would also split the
	// last rune into replacement bytes.
	goal := strings.Repeat("é", 200)

	var want struct {
		Keys      []string `json:"keys"`
		Goal      string   `json:"goal"`
		StartedAt string   `json:"started_at"`
	}
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pySlotPayloadSrc, &want,
		pyprobe.Arg(t, map[string]any{
			"loop_id": "loop-1", "goal": goal, "project": "proj"}))
	if len([]rune(want.Goal)) != 120 {
		t.Fatalf("CPython did not clip to 120 runes (%d) — the premise of this "+
			"test has changed", len([]rune(want.Goal)))
	}

	release, _, err := acquireProjectSlot(dir, "proj", "loop-1", goal)
	if err != nil {
		t.Fatalf("the slot must be free in a fresh dir: %v", err)
	}
	defer release()
	raw, err := os.ReadFile(filepath.Join(dir, "loop-proj.lock"))
	if err != nil {
		t.Fatal(err)
	}
	got, lerr := record.LoadsCleanOrdered(strings.TrimRight(string(raw), "\x00\n"))
	if lerr != nil {
		t.Fatalf("the lock is not clean JSON: %v", lerr)
	}

	var gotKeys []string
	for _, f := range got {
		gotKeys = append(gotKeys, f.Key)
	}
	if strings.Join(gotKeys, ",") != strings.Join(want.Keys, ",") {
		t.Errorf("lock keys %v, CPython writes %v", gotKeys, want.Keys)
	}
	if g, _ := got.Get("goal"); g != want.Goal {
		t.Errorf("goal clip: %d runes, CPython %d",
			len([]rune(g.(string))), len([]rune(want.Goal)))
	}
	// The stamp's SHAPE, not its value — the two clocks cannot agree.
	// "+00:00" and six digits, because observe.py feeds this to _age().
	s, _ := got.Get("started_at")
	if ss, _ := s.(string); !strings.HasSuffix(ss, "+00:00") ||
		!strings.HasSuffix(want.StartedAt, "+00:00") {
		t.Errorf("started_at %q, CPython %q — isoformat writes +00:00, not Z",
			s, want.StartedAt)
	}
}
