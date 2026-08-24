package scans

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/evolver"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// TestEscalationAndEventRowsAreJSONDumpsShaped drives the PRODUCTION path —
// VerifyAppliedSuggestions → notifyVerdict → notify.Emit → both writers —
// rather than the two private helpers this file used to call directly.
//
// That change is the point. r7's writer sweep missed these rows because it
// was an ENUMERATION of eight files rather than a search for the class, and
// the private helpers this test used to reach were a second copy of an
// emitter that had already been fixed once elsewhere. Driving the
// production entry point means the test keeps working when the writer moves
// again, and stops being evidence about a helper nobody calls.
//
// escalations.jsonl is the decreed headless escalation surface — "the thing
// you check when nothing else is configured" — and events.jsonl is the
// cross-runtime feed maro-observe tails. Python writes both with a bare
// json.dumps, and both were on encoding/json here: `>` in a target (every
// "A -> B" recommendation) came out `\u003e`, and the `→` the reason prose
// ALWAYS contains came out raw where json.dumps writes `\u2192`.
func TestEscalationAndEventRowsAreJSONDumpsShaped(t *testing.T) {
	ws := t.TempDir()
	seedSuggestion(t, ws, map[string]any{
		"applied_manually": true,
		"target":           "prefer a > b in the café path",
	})
	seedOutcomes(t, ws, applyTime, 10, 1, 8) // degraded → review_required
	VerifyAppliedSuggestions(ws, record.New(ws), map[string]any{}, "run-j",
		VerifyOptions{})

	esc := readOneLine(t, filepath.Join(ws, "output", "escalations.jsonl"))
	assertPythonShaped(t, "escalations.jsonl", esc)
	// ts and event_type lead, in that order, because the writer builds
	// `{"ts": ..., "event_type": ..., **payload}` and the payload supplies
	// neither. A row that carried them from the caller instead would sort
	// them in among the payload keys.
	if !strings.HasPrefix(esc, `{"ts": "`) {
		t.Fatalf("escalations.jsonl must lead with ts:\n%s", esc)
	}
	if !strings.Contains(esc, `", "event_type": "self_improvement_verdict", "action": `) {
		t.Fatalf("event_type must follow ts, ahead of the payload:\n%s", esc)
	}
	// The ts is the WRITER's clock, and the caller must not supply one.
	// The dict-splat semantics are real and ported — a payload key
	// OVERRIDES while keeping the leading position — so a caller that
	// stamps its own `ts` produces a row that is still perfectly shaped
	// and carries the wrong time. Every check above would pass it.
	if !strings.HasPrefix(esc, `{"ts": "`+time.Now().UTC().Format("2006-01-02")) {
		t.Fatalf("the escalation ts is not today's — did a caller stamp it?\n%s",
			esc[:48])
	}

	ev := readOneLine(t, filepath.Join(ws, "memory", "events.jsonl"))
	assertPythonShaped(t, "events.jsonl", ev)
	// observe.write_event's dict-literal order, which a Go map lost.
	if !strings.HasPrefix(ev, `{"event_type": "self_improvement_verdict", "ts": "`) {
		t.Fatalf("events.jsonl key order is not write_event's:\n%s", ev)
	}
}

func assertPythonShaped(t *testing.T, name, line string) {
	t.Helper()
	// The six literal characters, not the rune: this is the ESCAPE
	// sequence encoding/json emits and json.dumps never does.
	if strings.Contains(line, `\u003e`) {
		t.Fatalf("%s is HTML-escaped: no CPython writer produces \\u003e for `>`\n%s",
			name, line)
	}
	if !strings.Contains(line, `caf\u00e9`) {
		t.Fatalf("%s is not ensure_ascii: json.dumps escapes é\n%s", name, line)
	}
	if !strings.Contains(line, `\u2192`) {
		t.Fatalf("%s is not ensure_ascii: json.dumps escapes the → in the reason\n%s",
			name, line)
	}
	// Spans the item separator: a needle that stopped at a closing quote
	// would survive a mutant that compacted only `, ` (mission-r8 battery).
	if !strings.Contains(line, `", "`) {
		t.Fatalf("%s does not carry json.dumps' separators\n%s", name, line)
	}
	if strings.Contains(line, "\n") {
		t.Fatalf("%s row must be ONE line\n%s", name, line)
	}
}

func readOneLine(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("no %s: %v", path, err)
	}
	return strings.TrimRight(string(raw), "\n")
}

// TestVerifyRevertFailedNamesTheDetail pins the sentence a human reads when
// an auto-revert could not happen — the one case where the escalation says
// "the change is still live" and the reader has to act.
//
// `rates.get('revert_detail', 'no behavioral rollback')` is a PRESENCE
// default: it fires only when the key is absent. An earlier port
// substituted on the empty string as well, which reads like a kindness and
// is not — a producer that records an explicitly empty revert_detail is
// saying "I have nothing to tell you", and rewriting that into "no
// behavioral rollback" invents a cause the system never determined.
func TestVerifyRevertFailedNamesTheDetail(t *testing.T) {
	for _, c := range []struct {
		name  string
		rates map[string]any
		want  string
	}{
		{"absent takes the default", map[string]any{},
			"could NOT be auto-reverted (no behavioral rollback)"},
		{"present wins", map[string]any{"revert_detail": "the file was hand-edited"},
			"could NOT be auto-reverted (the file was hand-edited)"},
		{"present-but-empty stays empty", map[string]any{"revert_detail": ""},
			"could NOT be auto-reverted ()"},
	} {
		t.Run(c.name, func(t *testing.T) {
			ws := t.TempDir()
			rates := map[string]any{
				"stuck_rate_before": 0.1, "stuck_rate_after": 0.8,
			}
			for k, v := range c.rates {
				rates[k] = v
			}
			notifyVerdict(ws, evolver.Suggestion{
				SuggestionID: "v1", Category: "prompt_tweak", Target: "all",
			}, "revert_failed", true, rates)

			esc := readOneLine(t, filepath.Join(ws, "output", "escalations.jsonl"))
			var row map[string]any
			if err := json.Unmarshal([]byte(esc), &row); err != nil {
				t.Fatalf("escalation row is not JSON: %v\n%s", err, esc)
			}
			reason, _ := row["reason"].(string)
			if !strings.Contains(reason, c.want) {
				t.Fatalf("reason = %q\nwant it to contain %q", reason, c.want)
			}
			// The rest of the sentence is what makes it actionable — the
			// reader must be told the change did not come out.
			if !strings.Contains(reason, "Manual repair needed: the change is still live.") {
				t.Fatalf("the reason no longer says the change is live: %q", reason)
			}
		})
	}
}
