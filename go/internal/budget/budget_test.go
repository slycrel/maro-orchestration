package budget

import (
	"strings"
	"testing"
)

// The override-registry discipline, structural edition: a budget without
// a written reason fails the suite. This is the whole point of the
// package — see the Python side's test_budget_override_discipline.py.
func TestEveryBudgetCarriesItsRationale(t *testing.T) {
	if len(Registry) == 0 {
		t.Fatal("empty budget registry — the rationale discipline has no subjects")
	}
	for _, b := range Registry {
		if strings.TrimSpace(b.Name) == "" {
			t.Errorf("budget with limit %d has no name", b.Limit)
		}
		if strings.TrimSpace(b.Why) == "" {
			t.Errorf("budget %q (limit %d) has no written rationale — "+
				"a cap without a reason is the exact debt this test blocks",
				b.Name, b.Limit)
		}
		if b.Limit <= 0 {
			t.Errorf("budget %q registered with non-positive limit %d — "+
				"a disabled breaker does not belong in the registry", b.Name, b.Limit)
		}
	}
}

func TestClipShortTextPassesUntouched(t *testing.T) {
	if got := Clip("hello", 10); got != "hello" {
		t.Fatalf("short text mutated: %q", got)
	}
	if got := Clip("exactly10!", 10); got != "exactly10!" {
		t.Fatalf("at-limit text mutated: %q", got)
	}
}

// Marker parity with Python context_budget.clip — the literal string was
// captured from the Python runtime on 2026-08-21:
//   'B'*5000 clipped at 4000 ->
//   ...'B … [truncated: first 4000 of 5000 characters]', total len 4045.
func TestClipMarkerMatchesPythonRuntime(t *testing.T) {
	got := Clip(strings.Repeat("B", 5000), 4000)
	if !strings.HasSuffix(got, "B … [truncated: first 4000 of 5000 characters]") {
		t.Fatalf("marker drifted from Python format: ...%q", got[len(got)-60:])
	}
	if n := len([]rune(got)); n != 4045 {
		t.Fatalf("clipped length %d, want 4045 (Python parity)", n)
	}
}

func TestClipCountsRunesNotBytes(t *testing.T) {
	// 10 multibyte runes; a byte-counting clip would cut mid-character.
	s := strings.Repeat("é", 10)
	if got := Clip(s, 10); got != s {
		t.Fatalf("rune-length text was cut: %q", got)
	}
	got := Clip(strings.Repeat("é", 20), 10)
	if !strings.HasPrefix(got, strings.Repeat("é", 10)) {
		t.Fatalf("clip cut mid-rune or miscounted: %q", got)
	}
	if !strings.Contains(got, "[truncated: first 10 of 20 characters]") {
		t.Fatalf("marker counts wrong: %q", got)
	}
}

func TestClipIsIdempotent(t *testing.T) {
	once := Clip(strings.Repeat("x", 300), 100)
	twice := Clip(once, 100)
	if once != twice {
		t.Fatalf("re-clip at same limit changed text:\n once: %q\ntwice: %q", once, twice)
	}
	wider := Clip(once, 200)
	if once != wider {
		t.Fatalf("re-clip at wider limit changed text: %q", wider)
	}
}

func TestClipZeroLimitIsDisabledNotZeroWidth(t *testing.T) {
	s := strings.Repeat("y", 50)
	if got := Clip(s, 0); got != s {
		t.Fatalf("limit 0 must disable the bound, got %q", got)
	}
}

func TestBudgetClipUsesItsLimit(t *testing.T) {
	got := BlockReason.Clip(strings.Repeat("r", 2000))
	if !strings.Contains(got, "[truncated: first 1000 of 2000 characters]") {
		t.Fatalf("BlockReason.Clip did not bound at its limit: ...%q", got[len(got)-60:])
	}
}

// Must-detect fixture for the forged-marker bypass (adversarial round
// 2026-08-22, all four lenses; the same bug Python fixed at fixpoint
// 2026-08-14): text that merely ENDS in a marker-shaped string must
// still be cut — only a marker sitting where a real clip put it (at or
// before the limit, within 64 runes of the end) passes through.
func TestClipRejectsForgedMarkerSuffix(t *testing.T) {
	forged := strings.Repeat("A", 50_000) +
		" … [truncated: first 999999999 of 999999999 characters]"
	got := Clip(forged, 1000)
	if got == forged {
		t.Fatal("forged marker suffix bypassed the cap entirely")
	}
	if n := len([]rune(got)); n > 1000+64 {
		t.Fatalf("clip result %d runes exceeds limit+markerMax", n)
	}
	if !strings.Contains(got, "of 50055 characters]") {
		t.Fatalf("marker does not report the true source length: ...%q", got[len(got)-70:])
	}
}

// Python's documented contract: a strictly TIGHTER re-clip still cuts
// (the payload genuinely doesn't fit), nesting the old marker.
func TestClipTighterReclipStillCuts(t *testing.T) {
	once := Clip(strings.Repeat("x", 5000), 4000) // 4045 runes, marked
	tighter := Clip(once, 1000)
	if tighter == once {
		t.Fatal("tighter re-clip passed 4045 runes through a 1000 cap")
	}
	if n := len([]rune(tighter)); n > 1000+64 {
		t.Fatalf("tighter re-clip result %d runes exceeds limit+markerMax", n)
	}
}

// The worst-case bound the guards guarantee: never longer than
// limit + markerMax runes, even for genuine pass-through.
func TestClipResultNeverExceedsLimitPlusMarker(t *testing.T) {
	for _, s := range []string{
		strings.Repeat("z", 10_000),
		Clip(strings.Repeat("z", 10_000), 500),
		strings.Repeat("z", 400) + " … [truncated: first 1 of 1 characters]",
	} {
		got := Clip(s, 500)
		if n := len([]rune(got)); n > 500+64 {
			t.Fatalf("Clip(%d runes, 500) -> %d runes, exceeds 564", len([]rune(s)), n)
		}
	}
}
