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
