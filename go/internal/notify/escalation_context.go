package notify

// Escalation payload context — §9.6 simple-first (Jeremy decree 2026-07-27).
//
// An escalation asks a human ONE decision about ONE chasm, plus one line of
// goal-family context so the reader can judge whether this chasm is a
// recurring pattern worth a capability investment.
//
// Both helpers are pure and deterministic — no LLM calls, no config keys.
// The fields ride the existing "escalation" event ADDITIVELY: every
// consumer (the durable escalations.jsonl, the notify.command hook, the
// telegram bridge) sees them; none requires them.
//
// These are content-key PROSE, which is this port's recurring bug family:
// a byte-different sentence is a divergence even when every value in it is
// right, so the strings are transcribed rather than paraphrased and the
// tests diff them against CPython's own output.

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// decisionTemplates is one deterministic ask per emit point. The chasm
// specifics are interpolated; the option set names only actions the reader
// can actually take AT THAT POINT — no "resume" verb for a stuck run that
// has none.
var decisionTemplates = map[string]string{
	"blocked_step": "Decide this chasm: a step is blocked — %s. " +
		"Options: re-send the goal with guidance, or drop it.",
	"dispatch": "Decide this chasm: the run was parked before starting — %s. " +
		"Options: adjust the goal (or clear the blocker) and re-send, or drop it.",
	"director_escalation": "Decide: %s",
}

const reasonMax = 220

// DecisionLine is the single-chasm ask for an escalation payload.
//
// An unknown point gets a generic-but-honest ask rather than "" — an
// escalation with NO decision line is the pre-§9.6 shape this exists to
// replace, so falling back to silence would quietly restore it.
func DecisionLine(point, reason, step string) string {
	// `" ".join(str(reason).split())` — Python's bare split(), which is 29
	// code points to strings.Fields' 25. pytext.Split is the port's
	// existing answer; using strings.Fields here would leave U+001C..1F
	// (which arrive through pasted terminal output) embedded in one runtime
	// and collapsed in the other.
	short := runeHead(strings.Join(pytext.Split(reason), " "), reasonMax)
	if short == "" {
		// `... or "no reason recorded"` — the fallback fires on the EMPTY
		// string, after the clip, so a reason of pure whitespace lands here
		// too.
		short = "no reason recorded"
	}
	if step != "" {
		short = runeHead(strings.Join(pytext.Split(step), " "), 120) + " — " + short
	}
	tmpl, ok := decisionTemplates[point]
	if !ok {
		return "Decide: " + short
	}
	return fmt.Sprintf(tmpl, short)
}

// FamilyROILine is one line of goal-family context: how often this failure
// class recurs.
//
// Returns "" for empty/"healthy" classes and when the ledger is unreadable
// — the line is CONTEXT, and silence beats noise. A first occurrence is
// signal too ("first ... on record"): a brand-new chasm reads differently
// from a recurring one, and the two cases are told apart by looking at the
// file, not by the row count. Claiming "first" over a ledger that exists,
// is non-empty, and yielded nothing readable is exactly the noise this
// line exists to avoid.
//
// Counts raw ledger rows over the WHOLE file, and a row that would fail
// LoopDiagnosis construction still counts: it is still on record.
func FamilyROILine(ws, failureClass string, windowDays int) string {
	if failureClass == "" || failureClass == "healthy" {
		return ""
	}
	path := filepath.Join(ws, "memory", "diagnoses.jsonl")
	raw := readDiagnoses(path)
	if len(raw) == 0 {
		if st, err := os.Stat(path); err == nil && st.Size() > 0 {
			return ""
		} else if err != nil && !os.IsNotExist(err) {
			// Python's `except OSError: return ""`. A stat that fails for
			// any reason other than absence is not evidence of a cold
			// install.
			return ""
		}
	}
	total := 0
	recent := 0
	cutoff := time.Now().UTC().AddDate(0, 0, -windowDays)
	for _, d := range raw {
		if fc, _ := d["failure_class"].(string); fc != failureClass {
			continue
		}
		total++
		stamp, _ := d["recorded_at"].(string)
		if stamp == "" {
			continue // pre-V3 row without a stamp — all-time count only
		}
		ts, err := parseStamp(stamp)
		if err != nil {
			continue
		}
		if !ts.Before(cutoff) {
			recent++
		}
	}
	if total == 0 {
		return fmt.Sprintf("Family context: first '%s' failure on record.", failureClass)
	}
	// `diagnos{'is' if len(rows) == 1 else 'es'}` — the singular is
	// "diagnosis", and the branch is on the TOTAL, not the window count.
	noun := "diagnoses"
	if total == 1 {
		noun = "diagnosis"
	}
	line := fmt.Sprintf("Family context: '%s' has %d prior %s on record",
		failureClass, total, noun)
	if recent > 0 {
		line += fmt.Sprintf(", %d in the last %d days", recent, windowDays)
	}
	return line + "."
}

// readDiagnoses reads the whole ledger, bounded.
//
// Python's read_jsonl_tail reads the file unbounded; the 8MB cap is the
// port's standing answer for shared, co-resident-growable ledgers (an
// unbounded ReadFile on a file another process appends to is an OOM
// lever). When the cap bites, the OLDEST rows are the ones dropped, which
// makes "on record" a slight understatement rather than an overstatement —
// the safe direction for a line whose whole job is to say "this recurs".
func readDiagnoses(path string) []map[string]any {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return nil
	}
	offset := int64(0)
	if info.Size() > diagnosesTailBytes {
		offset = info.Size() - diagnosesTailBytes
	}
	if _, err := f.Seek(offset, io.SeekStart); err != nil {
		return nil
	}
	blob, err := io.ReadAll(f)
	if err != nil {
		return nil
	}
	if offset > 0 {
		if i := strings.IndexByte(string(blob), '\n'); i >= 0 {
			blob = blob[i+1:] // drop the torn first line
		}
	}
	var out []map[string]any
	for _, line := range strings.Split(string(blob), "\n") {
		if record.IsFrameBlank(line) {
			continue
		}
		row, err := record.LoadsClean(line)
		if err != nil {
			continue // a row that will not parse is not a row
		}
		out = append(out, row)
	}
	return out
}

const diagnosesTailBytes = 8 << 20

// parseStamp is datetime.fromisoformat over the shapes our writers emit; a
// NAIVE stamp is read as UTC, which is what Python's
// `ts.replace(tzinfo=timezone.utc)` does one line later.
func parseStamp(s string) (time.Time, error) {
	for _, layout := range []string{
		time.RFC3339Nano, time.RFC3339,
		"2006-01-02T15:04:05.999999999", "2006-01-02T15:04:05",
		"2006-01-02 15:04:05", "2006-01-02",
	} {
		if t, err := time.ParseInLocation(layout, s, time.UTC); err == nil {
			return t.UTC(), nil
		}
	}
	return time.Time{}, fmt.Errorf("unparseable timestamp %q", s)
}
