package playbook

import (
	"context"
	"os"
	"testing"
)

// Four pieces of production code that r10 proved correct AND unpinned: a
// mutant killed each behaviour and the whole suite still passed. Correct
// code with no pin is a regression waiting for a plausible edit, and each
// of these has a plausible edit.

// replaceAlarm replaces only the FIRST line carrying a given alarm key
// (`if not replaced and ...`). Dropping the `replaced` guard collapses
// every same-key line to identical text — and duplicate same-key alarms
// are precisely the pre-mechanism accretion the alarm design exists to
// clean up, so the state is reachable in a live playbook.
func TestOnlyTheFirstLineWithAnAlarmKeyIsReplaced(t *testing.T) {
	const doc = "# P\n\n## Signals\n\n" +
		"- cost high *(from s · alarm k:a @2026-08-20)*\n" +
		"- cost high too *(from s · alarm k:a @2026-08-20)*\n" +
		"- other *(from s · alarm k:b @2026-08-20)*\n\n" +
		"*Last updated: 2020-01-01*\n"

	pyWS := curateWorkspaceRaw(t, doc, "playbook:\n  alarm_ttl_days: 14\n")
	var want struct {
		File string `json:"file"`
	}
	runPython(t, pyWS, `
import json,sys
playbook.append_to_playbook(json.loads(sys.argv[2]), section='Signals',
                            source='s', key='k:a')
print(json.dumps({'file': playbook._playbook_path().read_text(encoding='utf-8')}))
`, &want, doc, "cost is now critical")

	goWS := curateWorkspaceRaw(t, doc, "playbook:\n  alarm_ttl_days: 14\n")
	if err := Append(goWS, nil, "cost is now critical", "Signals", "s", "k:a"); err != nil {
		t.Fatal(err)
	}
	gotB, err := os.ReadFile(Path(goWS))
	if err != nil {
		t.Fatal(err)
	}
	if string(gotB) != want.File {
		t.Fatalf("the file differs\n go %q\n py %q", gotB, want.File)
	}
	// Anti-vacuity: the fixture is only a first-match test if a SECOND
	// line with the same key survives untouched. If both lines ended up
	// identical, the mutant this pins would pass.
	if n := countLines(want.File, "- cost high too"); n != 1 {
		t.Fatalf("CPython did not leave the second k:a line intact (%d "+
			"occurrences); this fixture cannot see a collapse", n)
	}
}

// Curate expires BEFORE it dedups. Swapping the two lines is observable:
// two identical expired alarm lines report expired_alarms ['k:a','k:a']
// and removed_duplicates 0 in that order, and ['k:a'] / 1 in the other.
// Both numbers are written verbatim into the PLAYBOOK_CURATED captain's
// -log context and into curateSummary's rendered line, which the Python
// side reads.
func TestCurateExpiresBeforeItDedups(t *testing.T) {
	const doc = "# P\n\n## Signals\n\n" +
		"- cost high *(from s · alarm k:a @2001-01-01)*\n" +
		"- cost high *(from s · alarm k:a @2001-01-01)*\n\n" +
		"*Last updated: 2020-01-01*\n"
	const cfg = "playbook:\n  alarm_ttl_days: 14\n  curation_min_chars: 1000000\n"

	pyWS := curateWorkspaceRaw(t, doc, cfg)
	var want struct {
		Stats map[string]any `json:"stats"`
	}
	runPython(t, pyWS, `
import json,sys
print(json.dumps({'stats': playbook.curate_playbook(force=True)}))
`, &want, doc)

	goWS := curateWorkspaceRaw(t, doc, cfg)
	got := Curate(context.Background(), goWS, nil, nil, true)
	if got == nil {
		t.Fatal("Go declined to curate where CPython produced stats")
	}

	wantExpired, _ := want.Stats["expired_alarms"].([]any)
	if len(got.ExpiredAlarms) != len(wantExpired) {
		t.Errorf("expired_alarms: go %v, py %v", got.ExpiredAlarms, wantExpired)
	}
	if float64(got.RemovedDuplicates) != want.Stats["removed_duplicates"] {
		t.Errorf("removed_duplicates: go %d, py %v — this is the number that "+
			"flips when expiry and dedup swap order",
			got.RemovedDuplicates, want.Stats["removed_duplicates"])
	}
	// Anti-vacuity: expire-then-dedup yields TWO expired keys and ZERO
	// removed duplicates. If CPython reported one of each, the ordering
	// would be unobservable on this fixture and the pin would be theatre.
	if len(wantExpired) != 2 || want.Stats["removed_duplicates"] != float64(0) {
		t.Fatalf("this fixture cannot see the ordering: CPython reported "+
			"expired=%v removed=%v; expire-before-dedup must give 2 and 0",
			wantExpired, want.Stats["removed_duplicates"])
	}
}

// Seed NEVER overwrites. Every in-package caller pre-checks the file's
// existence, so no fixture ever called Seed on an existing document and
// deleting the guard survived the suite. Seed is EXPORTED, and it is the
// only verb here that writes the whole document without archiving first
// — so a future caller (a `maro playbook init`, a workspace bootstrap)
// landing after the guard was removed destroys an operator's playbook
// with no restorable copy.
func TestSeedNeverOverwritesAnExistingPlaybook(t *testing.T) {
	const mine = "# My own playbook\n\n## Cost\n\n- hand written\n"
	ws := curateWorkspaceRaw(t, mine, "playbook:\n  alarm_ttl_days: 14\n")

	if err := Seed(ws); err != nil {
		t.Fatalf("Seed on an existing file should be a no-op, got %v", err)
	}
	after, err := os.ReadFile(Path(ws))
	if err != nil {
		t.Fatal(err)
	}
	if string(after) != mine {
		t.Fatalf("Seed overwrote an existing playbook\n got %q\n want %q",
			after, mine)
	}

	// The other half: on an ABSENT file it must actually write, or the
	// assertion above would pass against a Seed that never writes at all.
	empty := t.TempDir()
	if err := Seed(empty); err != nil {
		t.Fatal(err)
	}
	seeded, err := os.ReadFile(Path(empty))
	if err != nil {
		t.Fatalf("Seed wrote nothing to an empty workspace: %v", err)
	}
	if len(seeded) == 0 || string(seeded) == mine {
		t.Fatalf("Seed did not write its own seed bytes: %q", seeded)
	}
}

func countLines(text, prefix string) int {
	n := 0
	for _, l := range splitLines(text) {
		if len(l) >= len(prefix) && l[:len(prefix)] == prefix {
			n++
		}
	}
	return n
}

func splitLines(s string) []string {
	var out []string
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			out = append(out, s[start:i])
			start = i + 1
		}
	}
	return append(out, s[start:])
}
