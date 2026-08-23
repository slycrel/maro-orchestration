package evolver

import (
	"os"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/playbook"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// A held guidance-only guardrail's block_reason must name the cause that
// ACTUALLY held it. `landed` is false for three reasons — below the
// confidence gate, a category with no playbook section, or an Append that
// failed — and the reason string used to assert the first one
// unconditionally (adversarial r10 LOW).
//
// This row is durable and operator-facing. A confident wrong cause is
// worse than a vague right one: it sends someone to lower a confidence
// threshold the suggestion had already cleared, while the real fault (a
// 30s fail-closed lock timeout against a concurrent Python writer, an
// unreadable file, a full disk) goes unread.
func TestAHeldGuardrailsBlockReasonNamesTheRealCause(t *testing.T) {
	t.Run("below the confidence gate says so", func(t *testing.T) {
		ws := t.TempDir()
		rec := record.New(ws)
		mustSave(t, ws, baseSuggestion("g-low", "new_guardrail", "all",
			"vague advice", 0.5))
		if _, err := Apply(ws, rec, nil, "g-low", true); err != nil {
			t.Fatal(err)
		}
		s := GetSuggestion(ws, "g-low")
		if s.Status != "held_for_review" {
			t.Fatalf("precondition: want held, got %+v", s)
		}
		if !strings.Contains(s.BlockReason, "confidence") ||
			!strings.Contains(s.BlockReason, "0.7") {
			t.Errorf("block_reason should name the confidence gate, got %q",
				s.BlockReason)
		}
		if strings.Contains(s.BlockReason, "append failed") {
			t.Errorf("block_reason names the wrong cause: %q", s.BlockReason)
		}
	})

	t.Run("an append failure does NOT blame the confidence gate", func(t *testing.T) {
		ws := t.TempDir()
		rec := record.New(ws)

		// Make playbook.Append fail deterministically without touching
		// permissions (this suite may run as a user for whom 0o000 is
		// advisory): a DIRECTORY where the document belongs makes the
		// read return EISDIR on every platform.
		if err := os.MkdirAll(playbook.Path(ws), 0o755); err != nil {
			t.Fatal(err)
		}

		// 0.9 is ABOVE the gate, so any mention of confidence here is a
		// false statement about why the row was held.
		mustSave(t, ws, baseSuggestion("g-hi", "new_guardrail", "all",
			"avoid destructive deletes", 0.9))
		if _, err := Apply(ws, rec, nil, "g-hi", true); err != nil {
			t.Fatal(err)
		}
		s := GetSuggestion(ws, "g-hi")
		if s.Status != "held_for_review" {
			t.Fatalf("precondition: an un-landable guardrail must still be "+
				"held, got %+v", s)
		}
		if strings.Contains(s.BlockReason, "below the 0.7") {
			t.Errorf("block_reason blames the confidence gate for a 0.9 "+
				"suggestion that cleared it: %q", s.BlockReason)
		}
		if !strings.Contains(s.BlockReason, "append failed") {
			t.Errorf("block_reason should name the append failure, got %q",
				s.BlockReason)
		}
	})
}
