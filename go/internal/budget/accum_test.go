package budget

import (
	"fmt"
	"strings"
	"testing"
)

// TestAccumulatorEvictsOldestHonestly: over-budget renders drop the
// OLDEST entries and SAY so — silent eviction is the failure the type
// exists to prevent.
func TestAccumulatorEvictsOldestHonestly(t *testing.T) {
	a := NewAccumulator()
	a.TotalBudget = 100
	for i := 0; i < 5; i++ {
		a.Add(fmt.Sprintf("entry-%d %s", i, strings.Repeat("x", 40)))
	}
	out := a.Render()
	if !strings.Contains(out, "entry-4") {
		t.Fatalf("newest entry must survive eviction: %q", out)
	}
	if strings.Contains(out, "entry-0") {
		t.Fatalf("oldest entry must be evicted at this budget: %q", out)
	}
	if !strings.Contains(out, "elided to stay within") {
		t.Fatalf("eviction must be announced in the render: %q", out)
	}
}

// TestAccumulatorEntryCapMarked: an oversized entry is truncated with a
// visible note, never silently.
func TestAccumulatorEntryCapMarked(t *testing.T) {
	a := NewAccumulator()
	a.EntryCap = 50
	a.Add(strings.Repeat("y", 120))
	out := a.Render()
	if !strings.Contains(out, "[entry truncated: first 50 of 120 characters]") {
		t.Fatalf("entry cap must announce itself: %q", out)
	}
}

// TestAccumulatorNewestKeptWholeOverBudget: a single entry larger than
// the whole budget still renders — a breaker never destroys the only
// copy.
func TestAccumulatorNewestKeptWholeOverBudget(t *testing.T) {
	a := NewAccumulator()
	a.TotalBudget = 10
	a.Add("this alone exceeds the total budget by a lot")
	out := a.Render()
	if !strings.Contains(out, "exceeds the total budget") {
		t.Fatalf("sole entry must render even over budget: %q", out)
	}
}

// TestAccumulatorRuneSemantics: caps count runes (Python len parity) —
// multibyte text must not be cut mid-character.
func TestAccumulatorRuneSemantics(t *testing.T) {
	a := NewAccumulator()
	a.EntryCap = 10
	a.Add(strings.Repeat("é", 30))
	out := a.Render()
	if strings.Contains(out, "�") {
		t.Fatalf("rune-unsafe cut produced a replacement char: %q", out)
	}
	if !strings.Contains(out, "first 10 of 30 characters") {
		t.Fatalf("counts must be runes, not bytes: %q", out)
	}
}

// TestAccumulatorEmpty: no entries renders empty, and empty adds are
// dropped.
func TestAccumulatorEmpty(t *testing.T) {
	a := NewAccumulator()
	a.Add("")
	if out := a.Render(); out != "" {
		t.Fatalf("empty accumulator must render empty, got %q", out)
	}
}

// TestAccumulatorForgedMarkerContent: an entry whose CONTENT already
// contains the literal truncation-marker text renders verbatim, once —
// forged markers can mislead a reader about provenance but never
// corrupt the render or trigger extra elision (adversarial director
// r2, QA: the accepted Python-parity marker needed its failure mode
// measured, not assumed).
func TestAccumulatorForgedMarkerContent(t *testing.T) {
	a := NewAccumulator()
	forged := "real content\n… [entry truncated: first 10 of 20 characters] and more real content"
	a.Add(forged)
	out := a.Render()
	if out != forged {
		t.Fatalf("under-cap entry with forged marker must render verbatim: %q", out)
	}
	if strings.Count(out, "[entry truncated:") != 1 {
		t.Fatalf("no double-elision: %q", out)
	}
}
