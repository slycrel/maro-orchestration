package playbook

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Curation reads three config values, and every one of them was ported as
// a typed getter where Python writes `int(...)`. These are the fixtures
// adversarial r9 used to show the two are different functions: a
// non-integral TTL, an explicit null, and a quoted number.
//
// The shipped suite could not see any of it, because curateWorkspace
// always wrote well-typed integers.

// curateWorkspaceRaw is curateWorkspace with the config.yml supplied
// verbatim, so a fixture can write a value no typed helper would produce.
func curateWorkspaceRaw(t *testing.T, doc, cfg string) string {
	t.Helper()
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	t.Setenv("MARO_USER_DIR", filepath.Join(t.TempDir(), "user"))
	if err := os.WriteFile(filepath.Join(ws, "config.yml"), []byte(cfg), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(ws, "playbook.md"), []byte(doc), 0o644); err != nil {
		t.Fatal(err)
	}
	return ws
}

func TestTheNumericConfigGatesMatchPythonsIntCall(t *testing.T) {
	// A document with BOTH an expirable alarm and a duplicate, so the two
	// gates have visibly different consequences: the TTL decides the
	// alarm, and either failure abandons the dedup too.
	doc := "# P\n\n## Signals\n\n- x · alarm k:a @2001-01-01\n\n" +
		"## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"

	for _, tc := range []struct {
		name string
		cfg  string
	}{
		{"a non-integral TTL truncates",
			"playbook:\n  alarm_ttl_days: 7.5\n  curation_min_chars: 1073741824\n"},
		{"an explicitly null TTL raises and abandons the pass",
			"playbook:\n  alarm_ttl_days: null\n  curation_min_chars: 1073741824\n"},
		{"a quoted min_chars parses",
			"playbook:\n  alarm_ttl_days: 14\n  curation_min_chars: \"1073741824\"\n"},
		{"a null min_chars raises and abandons the pass",
			"playbook:\n  alarm_ttl_days: 14\n  curation_min_chars: null\n"},
		{"an unparseable TTL raises",
			"playbook:\n  alarm_ttl_days: \"soon\"\n  curation_min_chars: 1073741824\n"},
		{"a negative TTL expires everything, in both",
			"playbook:\n  alarm_ttl_days: -1\n  curation_min_chars: 1073741824\n"},
		{"an absent TTL falls back to the default without calling int()",
			"playbook:\n  curation_min_chars: 1073741824\n"},
		{"a boolean TTL is int(True) == 1",
			"playbook:\n  alarm_ttl_days: true\n  curation_min_chars: 1073741824\n"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			pyWS := curateWorkspaceRaw(t, doc, tc.cfg)
			var want pyCurateResult
			runPython(t, pyWS, pyCurateSnippet, &want, doc)

			goWS := curateWorkspaceRaw(t, doc, tc.cfg)
			assertCurateAgrees(t, goWS,
				Curate(context.Background(), goWS, nil, nil, true), want)
		})
	}
}

// The table above proves int() RAISES where Get would not. It does not
// prove int() CONVERTS the way Python's does, because every one of its
// alarms is stamped 2001 — expired under any TTL the fixtures produce, so
// truncate-vs-round and True-vs-False are invisible.
//
// Mutation found exactly that: `math.Trunc` → `math.Round` and `int(True)`
// → 0 both survived the whole suite. These fixtures put the alarm ON the
// boundary the conversion moves.
//
// The stamp is derived from the clock rather than written down, because
// what discriminates is the alarm's AGE, and a fixed date's age changes
// every day this test runs.
func TestTheNumericConversionsMatchPythonsIntCall(t *testing.T) {
	for _, tc := range []struct {
		name    string
		value   string // the YAML int() is asked to convert
		control string // the spelling a WRONG conversion would produce
		ageDays int    // how old the alarm is, in whole days
	}{
		{"a fractional TTL truncates rather than rounding", "7.5", "8", 7},
		{"a true TTL is int(True) == 1, not 0", "true", "0", 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			day0 := utcNow().Format("2006-01-02")
			stamp := utcNow().AddDate(0, 0, -tc.ageDays).Format("2006-01-02")
			doc := "# P\n\n## Signals\n\n- x · alarm k:a @" + stamp + "\n\n" +
				"## Cost\n\n- keep\n\n*Last updated: 2020-01-01*\n"
			cfg := func(v string) string {
				return "playbook:\n  alarm_ttl_days: " + v +
					"\n  curation_min_chars: 1073741824\n"
			}

			var want, control pyCurateResult
			runPython(t, curateWorkspaceRaw(t, doc, cfg(tc.value)),
				pyCurateSnippet, &want, doc)
			runPython(t, curateWorkspaceRaw(t, doc, cfg(tc.control)),
				pyCurateSnippet, &control, doc)

			// The fixture must FLIP between the right conversion and the
			// wrong one. Without this, a Go that rounds passes by agreeing
			// with a Python that truncates on an input where both do the
			// same thing — which is how the mutants survived.
			if expired(want) == expired(control) {
				t.Fatalf("int(%s) and %s reach the same verdict on a %d-day-old "+
					"alarm; this fixture discriminates nothing",
					tc.value, tc.control, tc.ageDays)
			}

			goWS := curateWorkspaceRaw(t, doc, cfg(tc.value))
			got := Curate(context.Background(), goWS, nil, nil, true)
			if utcNow().Format("2006-01-02") != day0 {
				t.Skip("the UTC date rolled over mid-test — the two runs " +
					"computed different cutoffs, which is clock skew, not a " +
					"port defect")
			}
			assertCurateAgrees(t, goWS, got, want)
		})
	}
}

// expired reports whether the alarm left the document.
func expired(r pyCurateResult) bool {
	return r.Stats != nil && len(r.Stats.ExpiredAlarms) > 0
}

// The table above is only worth its runtime if the cases actually differ
// from each other. If every config produced the same curation, it would
// pass against a Curate that ignored config entirely.
func TestTheConfigCorpusReachesBothOutcomes(t *testing.T) {
	doc := "# P\n\n## Signals\n\n- x · alarm k:a @2001-01-01\n\n" +
		"## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"

	curated, abandoned := 0, 0
	for _, cfg := range []string{
		"playbook:\n  alarm_ttl_days: 7.5\n  curation_min_chars: 1073741824\n",
		"playbook:\n  alarm_ttl_days: null\n  curation_min_chars: 1073741824\n",
		"playbook:\n  alarm_ttl_days: 14\n  curation_min_chars: null\n",
	} {
		ws := curateWorkspaceRaw(t, doc, cfg)
		if Curate(context.Background(), ws, nil, nil, true) != nil {
			curated++
		} else {
			abandoned++
		}
	}
	if curated == 0 || abandoned == 0 {
		t.Fatalf("every config reached the same outcome (curated=%d "+
			"abandoned=%d); the table above discriminates nothing",
			curated, abandoned)
	}
}

// Curate takes a workspace ARGUMENT. Everything it reads must come from
// that workspace — the playbook, the archive dir, the lock, AND the
// config. Reading the gates from the ambient MARO_WORKSPACE made it
// half-scoped, and the destructive half: an ambient TTL expires alarms
// out of a document whose own config.yml said to keep them.
//
// Python cannot be compared here — its path and its config are both
// module-level, so this failure mode does not exist there. That makes it
// a Go-only invariant, and it needs a Go-only pin.
func TestCurateReadsTheConfigOfTheWorkspaceItWasHanded(t *testing.T) {
	doc := "# P\n\n## Signals\n\n- x · alarm k:a @2001-01-01\n\n" +
		"## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"

	// The AMBIENT workspace says: curation off, and expire everything.
	ambient := curateWorkspaceRaw(t, doc,
		"playbook:\n  curation_enabled: false\n  alarm_ttl_days: 1\n"+
			"  curation_min_chars: 1073741824\n")

	// The TARGET workspace says: curation on, and keep alarms ~274 years.
	target := t.TempDir()
	if err := os.WriteFile(filepath.Join(target, "config.yml"), []byte(
		"playbook:\n  curation_enabled: true\n  alarm_ttl_days: 100000\n"+
			"  curation_min_chars: 1073741824\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(target, "playbook.md"),
		[]byte(doc), 0o644); err != nil {
		t.Fatal(err)
	}
	if ambient == target {
		t.Fatal("the two workspaces must differ or this proves nothing")
	}

	// force=false, so the gate is live: the ambient config would return
	// nil outright.
	got := Curate(context.Background(), target, nil, nil, false)
	if got == nil {
		t.Fatal("Curate honoured the AMBIENT workspace's curation_enabled:false " +
			"instead of the target's true")
	}
	if len(got.ExpiredAlarms) != 0 {
		t.Errorf("the ambient 1-day TTL expired %v out of a workspace whose "+
			"own config.yml sets 100000 days", got.ExpiredAlarms)
	}
	if pb := Load(target); !strings.Contains(pb, "alarm k:a") {
		t.Errorf("the alarm was expired against the target's own config:\n%s", pb)
	}
	// And the duplicate WAS collapsed, so the run did real work rather
	// than bailing early for some unrelated reason.
	if got.RemovedDuplicates != 1 {
		t.Errorf("want the duplicate collapsed, got removed=%d", got.RemovedDuplicates)
	}
}
