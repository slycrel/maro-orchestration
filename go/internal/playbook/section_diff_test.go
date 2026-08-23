package playbook

import (
	"os"
	"testing"
)

// SectionText and ExpireStaleAlarms shipped with no differential at all:
// SectionText had no test in the package, and ExpireStaleAlarms's only
// test was a Go-only refusal assertion. Both are EXPORTED verbs that read
// and (for the second) rewrite the shared document, and neither had ever
// been compared against section_text / expire_stale_alarms.
//
// Both turned out correct. That is the point — "correct today, unpinned
// tomorrow" is the state this package keeps re-entering, and it is what
// let the newline divergence live behind twelve green differentials.

func TestSectionTextMatchesCPython(t *testing.T) {
	const doc = "# Operational Playbook\n\n" +
		"## Cost\n\n- watch spend *(from run-a)*\n- and again\n\n" +
		"## Quality\n\n- be careful\n\n" +
		"## Empty\n\n" +
		"## Trailing Tabs\t\n\n- reached via a tabbed header\n\n" +
		"*Last updated: 2020-01-01*\n"

	for _, section := range []string{
		"Cost",
		"Quality",
		"Empty",
		"Trailing Tabs",
		"Nonexistent",
		"cost",        // case matters: the header regex is not folded
		"Cost ",       // a trailing space in the ARGUMENT, not the header
		"",            // degenerate
		"## Cost",     // the caller passing the marker too
		"Operational", // an H1, not an H2
	} {
		t.Run("section "+section, func(t *testing.T) {
			ws := curateWorkspaceRaw(t, doc, "playbook:\n  alarm_ttl_days: 14\n")
			var want struct {
				Text string `json:"text"`
			}
			runPython(t, ws, `
import json,sys
print(json.dumps({'text': playbook.section_text(json.loads(sys.argv[2])) or ''}))
`, &want, doc, section)

			if got := SectionText(ws, section); got != want.Text {
				t.Errorf("SectionText(%q)\n go %q\n py %q", section, got, want.Text)
			}
		})
	}
}

func TestExpireStaleAlarmsMatchesCPython(t *testing.T) {
	const doc = "# P\n\n## Signals\n\n" +
		"- old reading *(from s · alarm k:a @2001-01-01)*\n" +
		"- fresh reading *(from s · alarm k:b @2099-01-01)*\n" +
		"- twin old *(from s · alarm k:c @2001-01-01)*\n" +
		"- not an alarm at all\n\n" +
		"## Cost\n\n- unrelated\n\n" +
		"*Last updated: 2020-01-01*\n"

	for _, ttl := range []int{0, 1, 14, 100000, -1} {
		t.Run("ttl", func(t *testing.T) {
			pyWS := curateWorkspaceRaw(t, doc, "playbook:\n  alarm_ttl_days: 14\n")
			var want struct {
				N    int    `json:"n"`
				File string `json:"file"`
			}
			runPython(t, pyWS, `
import json,sys
n = playbook.expire_stale_alarms(max_age_days=json.loads(sys.argv[2]))
print(json.dumps({
  'n': n,
  'file': playbook._playbook_path().read_text(encoding='utf-8'),
}))
`, &want, doc, ttl)

			goWS := curateWorkspaceRaw(t, doc, "playbook:\n  alarm_ttl_days: 14\n")
			got := ExpireStaleAlarms(goWS, ttl)
			if got != want.N {
				t.Errorf("ttl=%d expired: go %d, py %d", ttl, got, want.N)
			}
			after, err := os.ReadFile(Path(goWS))
			if err != nil {
				t.Fatal(err)
			}
			if string(after) != want.File {
				t.Errorf("ttl=%d the file differs\n go %q\n py %q",
					ttl, after, want.File)
			}
		})
	}
}
