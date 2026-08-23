package orch

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
	"testing"
)

func readSidecar(t *testing.T, ws, slug string) string {
	t.Helper()
	raw, err := os.ReadFile(doingPIDsPath(ws, slug))
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

// The stamp and the state flip are ONE transition, written under the same
// lock, so the sidecar can never claim an owner for an item that is not
// DOING. json.dumps(indent=2) with no trailing newline is the spelling
// Python writes; both runtimes rewrite this file.
func TestDoingStampIsWrittenAndClearedWithTheState(t *testing.T) {
	ws := seed(t, "p", "- [ ] a\n- [ ] b\n")
	if err := MarkItem(ws, "p", 0, StateDoing); err != nil {
		t.Fatal(err)
	}
	body := readSidecar(t, ws, "p")
	want := fmt.Sprintf("{\n  \"0\": {\n    \"pid\": %d,\n    \"at\": ", os.Getpid())
	if !strings.HasPrefix(body, want) || !strings.HasSuffix(body, "\n  }\n}") {
		t.Fatalf("indent=2 with no trailing newline:\n%q", body)
	}
	// Python stamps with a bare strftime("%…S%z"), whose offset carries no
	// colon. RFC3339's "+00:00" is the spelling Go reaches for by default
	// and it would make every Go-written entry a differently-stored row.
	atFormat := regexp.MustCompile(
		`"at": "\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}"`)
	if !atFormat.MatchString(body) {
		t.Fatalf("the %%z stamp must carry no colon:\n%s", body)
	}
	// A second DOING item appends; the first keeps its place.
	if err := MarkItem(ws, "p", 1, StateDoing); err != nil {
		t.Fatal(err)
	}
	body = readSidecar(t, ws, "p")
	if strings.Index(body, `"0"`) > strings.Index(body, `"1"`) {
		t.Fatalf("insertion order must be preserved:\n%s", body)
	}
	// Any other transition drops the entry — a stale owner would make a
	// live item look stranded and hand its work to a second executor.
	if err := MarkItem(ws, "p", 0, StateDone); err != nil {
		t.Fatal(err)
	}
	body = readSidecar(t, ws, "p")
	if strings.Contains(body, `"0"`) {
		t.Fatalf("the DONE flip must drop the stamp:\n%s", body)
	}
	if err := MarkItem(ws, "p", 1, StateTodo); err != nil {
		t.Fatal(err)
	}
	if got := readSidecar(t, ws, "p"); got != "{}" {
		t.Fatalf("an emptied sidecar is {}, the way json.dumps spells it: %q", got)
	}
}

// A field this runtime does not model — one the other runtime added, or
// an operator's note — must survive a rewrite. Dropping unknown keys is
// how a shared store silently loses the newer side's data.
func TestSidecarCarriesUnknownFieldsThroughARewrite(t *testing.T) {
	ws := seed(t, "p", "- [ ] a\n- [ ] b\n")
	seedSidecar(t, ws, "p", `{
  "0": {
    "pid": 1,
    "at": "2026-08-20T10:00:00+0000",
    "host": "mini2"
  }
}`)
	if err := MarkItem(ws, "p", 1, StateDoing); err != nil {
		t.Fatal(err)
	}
	body := readSidecar(t, ws, "p")
	if !strings.Contains(body, `"host": "mini2"`) {
		t.Fatalf("an unmodelled field was dropped:\n%s", body)
	}
	if strings.Index(body, `"0"`) > strings.Index(body, `"1"`) {
		t.Fatalf("the carried entry keeps its position:\n%s", body)
	}
	// And it is still valid JSON that Python can read back.
	var round map[string]map[string]any
	if err := json.Unmarshal([]byte(body), &round); err != nil {
		t.Fatalf("%v\n%s", err, body)
	}
	if round["0"]["host"] != "mini2" || round["1"]["pid"] == nil {
		t.Fatalf("%+v", round)
	}
}

func seedSidecar(t *testing.T, ws, slug, body string) {
	t.Helper()
	if err := os.WriteFile(doingPIDsPath(ws, slug), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// A DOING item whose owner died is a leaked lock. Two shapes count: a
// dead recorded pid, and NO entry at all — since every DOING flip stamps
// under the same lock, a missing entry means the item has no live owner
// either way.
func TestStrandedDoingItemsFindsBothLeakShapes(t *testing.T) {
	ws := seed(t, "p", "- [~] dead-owner\n- [~] no-entry\n- [~] alive\n- [ ] todo\n")
	// pid 1 is init: alive but not ours, so kill(1, 0) returns EPERM and
	// Python reads that as ALIVE. A dead pid is one we can prove gone.
	seedSidecar(t, ws, "p", fmt.Sprintf(`{
  "0": {"pid": %d, "at": "x"},
  "2": {"pid": %d, "at": "x"}
}`, deadPID(t), os.Getpid()))

	got, err := StrandedDoingItems(ws, "p")
	if err != nil {
		t.Fatal(err)
	}
	var texts []string
	for _, it := range got {
		texts = append(texts, it.Text)
	}
	if len(texts) != 2 || texts[0] != "dead-owner" || texts[1] != "no-entry" {
		t.Fatalf("%v", texts)
	}
	// A ledger with nothing DOING short-circuits without reading the
	// sidecar at all.
	ws2 := seed(t, "p", "- [ ] a\n")
	if got, err := StrandedDoingItems(ws2, "p"); err != nil || got != nil {
		t.Fatalf("%v %v", got, err)
	}
}

// deadPID returns a pid that is very unlikely to exist: a child we reaped.
func deadPID(t *testing.T) int {
	t.Helper()
	// pid 0x7FFFFFF0 is above any Linux pid_max default and cannot be a
	// live process, which is a deterministic "dead" without racing a real
	// pid's reuse.
	return 0x7FFFFFF0
}

// The probe has three outcomes and only one of them means gone. A
// process owned by ANOTHER user answers EPERM, and reading that as dead
// would let a drain loop revert an item another user's live executor is
// working on — the exact double-execution the DOING state exists to
// prevent. pid 1 is init: always running, never ours.
func TestAProcessOwnedByAnotherUserReadsAsAlive(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("as root every kill(1, 0) succeeds, so EPERM is unreachable")
	}
	if !pidAlive(1) {
		t.Fatal("init is alive; EPERM must not read as gone")
	}
	if pidAlive(int64(deadPID(t))) {
		t.Fatal("a pid above pid_max cannot be running")
	}
	ws := seed(t, "p", "- [~] owned-elsewhere\n")
	seedSidecar(t, ws, "p", `{"0": {"pid": 1, "at": "x"}}`)
	got, err := StrandedDoingItems(ws, "p")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Fatalf("an item owned by a live foreign process must not be stolen: %+v", got)
	}
}

// pid 0 is not a sentinel for "unknown": kill(0, sig) addresses the
// caller's own process GROUP and succeeds, so Python reads such an entry
// as ALIVE and leaves the item alone. Reproduced rather than corrected —
// the two runtimes must agree on which items a drain loop may steal.
func TestAZeroPIDReadsAsAliveJustAsPythonReadsIt(t *testing.T) {
	ws := seed(t, "p", "- [~] a\n")
	for _, entry := range []string{
		`{"0": {"pid": 0, "at": "x"}}`,
		`{"0": {"at": "x"}}`,   // missing -> `or 0` -> 0
		`{"0": {"pid": null}}`, // null   -> `or 0` -> 0
		`{"0": {"pid": ""}}`,   // ""     -> `or 0` -> 0
		`{"0": {"pid": "0"}}`,  // int("0")
	} {
		seedSidecar(t, ws, "p", entry)
		got, err := StrandedDoingItems(ws, "p")
		if err != nil {
			t.Fatalf("%s: %v", entry, err)
		}
		if len(got) != 0 {
			t.Errorf("%s: a zero pid reads as alive, got %d stranded", entry, len(got))
		}
	}
}

// A pid that is present but unreadable is an ERROR, not a default.
// Python's int() raises straight out of stranded_doing_items; softening
// it to "treat an unreadable pid as dead" would revert a LIVE item to
// TODO and hand its work to a second executor.
func TestAnUnreadablePIDIsLoudRatherThanAssumedDead(t *testing.T) {
	ws := seed(t, "p", "- [~] a\n")
	for _, entry := range []string{
		`{"0": {"pid": "not-a-number", "at": "x"}}`,
		`{"0": ["not", "an", "object"]}`,
	} {
		seedSidecar(t, ws, "p", entry)
		if _, err := StrandedDoingItems(ws, "p"); err == nil {
			t.Errorf("%s must be an error, not a silent steal", entry)
		}
	}
}

// The sidecar is forensics, so a corrupt one must not stop real work:
// Python's reader swallows every exception and returns {}. The cost is a
// stranded-item sweep that over-reports; the alternative is refusing to
// mark items because a diagnostic file is damaged.
func TestACorruptSidecarReadsAsEmptyRatherThanBlockingAMark(t *testing.T) {
	ws := seed(t, "p", "- [ ] a\n")
	seedSidecar(t, ws, "p", "{not json at all")
	if err := MarkItem(ws, "p", 0, StateDoing); err != nil {
		t.Fatalf("a corrupt sidecar must not fail the mark: %v", err)
	}
	body := readSidecar(t, ws, "p")
	if !strings.Contains(body, `"0"`) {
		t.Fatalf("the mark still stamps:\n%s", body)
	}
	raw, _ := os.ReadFile(NextPath(ws, "p"))
	if string(raw) != "- [~] a\n" {
		t.Fatalf("%q", raw)
	}
}

// A sidecar that parses PARTWAY and then fails is the dangerous shape:
// json.loads gives Python nothing at all, so keeping the entries read
// before the break would leave the two runtimes disagreeing about who
// owns an item — Go seeing a live owner where Python sees none. All or
// nothing, matching the other runtime.
func TestAPartiallyParseableSidecarIsDiscardedWholesale(t *testing.T) {
	ws := seed(t, "p", "- [~] a\n- [~] b\n")
	// In each shape entry "0" is well-formed and names a LIVE process, so
	// keeping it would leave item 0 unreclaimable. The three shapes break
	// at the three different places the walk can fail: a value that will
	// not decode, a document that ends mid-object, and a key that is not a
	// string.
	for _, broken := range []string{
		`{"0": {"pid": %d, "at": "x"}, "1": not-json}`,
		`{"0": {"pid": %d, "at": "x"},`,
		`{"0": {"pid": %d, "at": "x"}, 7: {}}`,
	} {
		seedSidecar(t, ws, "p", fmt.Sprintf(broken, os.Getpid()))
		got, err := StrandedDoingItems(ws, "p")
		if err != nil {
			t.Fatalf("%s: %v", broken, err)
		}
		if len(got) != 2 {
			t.Errorf("%s: a broken sidecar reads as empty in both runtimes, "+
				"so BOTH items are unowned; got %d stranded", broken, len(got))
		}
	}
}
