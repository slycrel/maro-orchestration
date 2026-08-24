package notify

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The events.jsonl PROJECTION — how a rich payload becomes the four fields
// write_event has room for. It is the only half of emit() that makes a
// judgement, and it had no test: `scans` carried a comment describing it and
// a call site that happened to pass the right strings by hand.

func eventRow(t *testing.T, ws string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(ws, "memory", "events.jsonl"))
	if err != nil {
		t.Fatalf("no events row: %v", err)
	}
	line := strings.TrimSpace(strings.Split(strings.TrimSpace(string(raw)), "\n")[0])
	var m map[string]any
	if err := json.Unmarshal([]byte(line), &m); err != nil {
		t.Fatalf("events row is not JSON: %v\n%s", err, line)
	}
	return m
}

func emitOne(t *testing.T, eventType string, payload map[string]any) map[string]any {
	t.Helper()
	ws := t.TempDir()
	Emit(context.Background(), ws, eventType, payload, Options{Cfg: map[string]any{}})
	return eventRow(t, ws)
}

// `str(payload.get("result_excerpt", payload.get("summary", "")))` is a
// PRESENCE default, not a truthiness one, and the difference is visible: a
// payload that carries result_excerpt=None projects the literal string
// "None", because that is what str(None) is.
//
// Reading it as "falsy means fall back" is the natural port and it is wrong
// — it silently changes which text reaches the substrate, and it changes it
// only for the payloads where a producer explicitly recorded "there was no
// excerpt", which is exactly when the distinction matters.
func TestDetailPrefersResultExcerptByPresenceNotTruth(t *testing.T) {
	for _, c := range []struct {
		name    string
		payload map[string]any
		want    string
	}{
		{"result_excerpt wins when present",
			map[string]any{"result_excerpt": "the excerpt", "summary": "the summary"},
			"the excerpt"},
		{"summary is used when result_excerpt is absent",
			map[string]any{"summary": "the summary"}, "the summary"},
		{"neither present projects the empty string",
			map[string]any{}, ""},
		// The three cases a truthiness reading gets wrong.
		{"a present nil result_excerpt projects None, not the summary",
			map[string]any{"result_excerpt": nil, "summary": "the summary"}, "None"},
		{"a present EMPTY result_excerpt wins over the summary",
			map[string]any{"result_excerpt": "", "summary": "the summary"}, ""},
		{"a present nil summary projects None",
			map[string]any{"summary": nil}, "None"},
		// str() of a non-string, which `default=str` never sees because
		// this happens before any JSON encoding.
		{"a numeric excerpt is str()'d",
			map[string]any{"result_excerpt": 42}, "42"},
		{"a bool excerpt gets Python's spelling",
			map[string]any{"result_excerpt": true}, "True"},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got := emitOne(t, "escalation", c.payload)["detail"]; got != c.want {
				t.Fatalf("detail = %q, want %q", got, c.want)
			}
		})
	}
}

// The same presence-vs-truth question on the goal field, which falls back
// from `goal` to `reason`.
func TestGoalFallsBackFromGoalToReason(t *testing.T) {
	for _, c := range []struct {
		name    string
		payload map[string]any
		want    string
	}{
		{"goal wins", map[string]any{"goal": "g", "reason": "r"}, "g"},
		{"reason is the fallback", map[string]any{"reason": "r"}, "r"},
		{"a present nil goal projects None, not the reason",
			map[string]any{"goal": nil, "reason": "r"}, "None"},
		{"neither is the empty string", map[string]any{}, ""},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got := emitOne(t, "escalation", c.payload)["goal"]; got != c.want {
				t.Fatalf("goal = %q, want %q", got, c.want)
			}
		})
	}
}

// run_verdict's detail is a SPECIAL CASE, and it exists because the generic
// projection produced an empty follow-up: a run_verdict payload carries
// neither result_excerpt nor summary, so a polling substrate received a
// blank row where the verdict was supposed to be (review 2026-08-13).
//
// A port that dropped the special case would leave the answer-first split
// half-delivered in a way nothing else notices — the row is still there,
// still well-formed, and says nothing.
func TestRunVerdictDetailCarriesTheVerdict(t *testing.T) {
	full := emitOne(t, "run_verdict", map[string]any{
		"handle_id":            "h-9",
		"goal_achieved":        true,
		"goal_verdict_source":  "judge",
		"answer_changed":       true,
		"goal_verdict_summary": "the answer now cites the right file",
	})["detail"]
	want := "[h-9] goal_achieved=True source=judge answer_changed; " +
		"the answer now cites the right file"
	if full != want {
		t.Fatalf("detail =\n %q\nwant\n %q", full, want)
	}

	// The two optional clauses are gated on TRUTHINESS here (Python's `if
	// payload.get(...)`), unlike the presence-gated fallbacks above — the
	// same payload read two different ways in two adjacent lines, which is
	// why both need pinning.
	bare := emitOne(t, "run_verdict", map[string]any{
		"handle_id":            "h-9",
		"goal_achieved":        false,
		"goal_verdict_source":  "",
		"answer_changed":       false,
		"goal_verdict_summary": "no change",
	})["detail"]
	if bare != "[h-9] goal_achieved=False; no change" {
		t.Fatalf("detail = %q", bare)
	}

	// Missing keys spell Python's None, not Go's zero values — an absent
	// goal_achieved is a producer bug, and the row should say so rather
	// than assert "False".
	empty := emitOne(t, "run_verdict", map[string]any{"handle_id": "h-9"})["detail"]
	if empty != "[h-9] goal_achieved=None; " {
		t.Fatalf("detail = %q", empty)
	}

	// And the generic projection must NOT apply to run_verdict even when
	// the payload happens to carry a summary — that is the bug this case
	// exists to prevent, restated as a test.
	shadowed, _ := emitOne(t, "run_verdict", map[string]any{
		"handle_id": "h-9", "summary": "a summary that must not win",
		"goal_achieved": true, "goal_verdict_summary": "the verdict",
	})["detail"].(string)
	if !strings.Contains(shadowed, "the verdict") ||
		strings.Contains(shadowed, "must not win") {
		t.Fatalf("the generic projection shadowed the verdict: %q", shadowed)
	}
}

// Both projected fields are BOUNDED, and the two bounds are different
// KINDS of cut on purpose.
//
// `goal` is write_event's own bare `goal[:80]` — a silent slice, its
// long-standing contract. `detail` goes through the clip breaker, which
// keeps `cap` characters and then APPENDS a marker naming what it cut, so
// the field is longer than its cap by design. Reading "the PIPE_BUF bound
// is 200" as "the string is at most 200" is the natural misreading, and it
// is wrong on both sides of the port.
func TestProjectedFieldsAreBoundedAndAnnounced(t *testing.T) {
	row := emitOne(t, "escalation", map[string]any{
		"reason":  strings.Repeat("g", 500),
		"summary": strings.Repeat("d", 500),
	})
	goal, _ := row["goal"].(string)
	detail, _ := row["detail"].(string)

	if goal != strings.Repeat("g", 80) {
		t.Fatalf("goal is not write_event's bare 80-rune slice: %q", goal)
	}
	if strings.Contains(goal, "\u2026") || strings.Contains(goal, "truncated") {
		t.Fatalf("goal now announces its cut; write_event's is a bare slice: %q", goal)
	}

	// The marker is the whole point of using a breaker instead of a slice:
	// a reader must be able to tell trimmed evidence from complete
	// evidence, and it names the source length so the reader knows how much
	// is missing.
	//
	// "of 343", not "of 500", and that is not a bug. The value is clipped
	// TWICE — at 300 by the notify projection, then at 200 by write_event —
	// and clip is idempotent only at the same or a WIDER cap. A strictly
	// tighter re-clip genuinely has to cut again, so the outer marker
	// describes the 343-character string it was handed rather than the
	// 500-character original, and the first marker survives inside the
	// payload. Python nests it the same way, which is the only reason this
	// is a fact about the format and not a divergence.
	want := strings.Repeat("d", 200) + " \u2026 [truncated: first 200 of 343 characters]"
	if detail != want {
		t.Fatalf("detail:\n got %q\nwant %q", detail[190:], want[190:])
	}
}
