package budget

import (
	"fmt"
	"strings"
)

// Accumulator ports Python context_budget.ContextBudget: accumulate
// prose entries for a prompt, bounded and HONEST about it. Entries are
// kept whole where possible; when the total exceeds the budget the
// OLDEST are dropped and Render says how many went — silent eviction is
// the lie this type exists to prevent (the director's completed-context
// re-send grows quadratically in ticket count otherwise).
//
// Counts are runes, matching Python len() semantics, so mixed-runtime
// renders elide at the same points.
type Accumulator struct {
	// TotalBudget bounds the rendered block (Python
	// DEFAULT_TOTAL_BUDGET). Zero means the caller forgot: New sets it.
	TotalBudget int
	// EntryCap bounds one entry with a visible truncation note (Python
	// DEFAULT_ENTRY_CAP).
	EntryCap  int
	Separator string
	entries   []string
}

// NewAccumulator returns an Accumulator at the Python defaults —
// entry cap 4000 (StepResult's limit), total 24000 (StepContextTotal's
// limit). One number, two runtimes.
func NewAccumulator() *Accumulator {
	return &Accumulator{
		TotalBudget: StepContextTotal.Limit,
		EntryCap:    StepResult.Limit,
		Separator:   "\n\n",
	}
}

// Add appends one entry, capping it (visibly) if oversized. Empty
// entries are dropped, matching Python.
func (a *Accumulator) Add(entry string) {
	if entry == "" {
		return
	}
	// Deliberately NOT budget.Clip: the entry marker is byte-compatible
	// with Python ContextBudget.add's SECOND marker format ("[entry
	// truncated: …]"), so mixed-runtime renders stay parseable by one
	// reader. Clip's forged-marker idempotency guard doesn't transfer —
	// entries are cut exactly once at Add and never re-clipped
	// (adversarial director r1: named, accepted).
	if r := []rune(entry); len(r) > a.EntryCap {
		entry = fmt.Sprintf("%s\n… [entry truncated: first %d of %d characters]",
			string(r[:a.EntryCap]), a.EntryCap, len(r))
	}
	a.entries = append(a.entries, entry)
}

// Render returns the context block, oldest entries evicted to fit the
// budget, with an honest elision header when anything went. Newest
// entries survive (walk newest → oldest); the newest entry is always
// kept whole even if it alone exceeds the budget — a breaker never
// destroys the only copy.
func (a *Accumulator) Render() string {
	if len(a.entries) == 0 {
		return ""
	}
	var kept []string
	used := 0
	for i := len(a.entries) - 1; i >= 0; i-- {
		entry := a.entries[i]
		cost := len([]rune(entry)) + len([]rune(a.Separator))
		if len(kept) > 0 && used+cost > a.TotalBudget {
			break
		}
		kept = append(kept, entry)
		used += cost
	}
	// reverse back to oldest-first
	for i, j := 0, len(kept)-1; i < j; i, j = i+1, j-1 {
		kept[i], kept[j] = kept[j], kept[i]
	}
	dropped := len(a.entries) - len(kept)
	body := strings.Join(kept, a.Separator)
	if dropped > 0 {
		plural := "ies"
		if dropped == 1 {
			plural = "y"
		}
		return fmt.Sprintf("[%d earlier entr%s elided to stay within "+
			"the context budget; the most recent %d follow]%s%s",
			dropped, plural, len(kept), a.Separator, body)
	}
	return body
}
