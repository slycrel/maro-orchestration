package provenance

import "testing"

// Pinned against the same shapes tests/test_lesson_provenance.py pins —
// the two regex files are a shared contract.
func TestClassifyPromptDerivedShapes(t *testing.T) {
	promptShaped := []string{
		"When a prompt explicitly says 'do not escalate/stop', treat that as a hard constraint",
		"The instructions clearly state that partial results are unacceptable",
		"Directives from the operator must be obeyed",
		"If told to continue, follow them to the letter",
	}
	for _, text := range promptShaped {
		if got := Classify(text, "", ""); got != MintedFromPrompt {
			t.Errorf("prompt-derived shape classified %q: %q", got, text)
		}
	}
}

func TestClassifyOutcomeShapesStayClean(t *testing.T) {
	clean := []string{
		"a task specifies an exact output contract, verify against it before returning",
		"rate limits are a hard constraint on parallelism",
		"Jina Reader returns cleaner text than raw HTML for LLM consumption",
	}
	for _, text := range clean {
		if got := Classify(text, "", ""); got != MintedFromOutcome {
			t.Errorf("outcome-shaped lesson quarantined: %q", text)
		}
	}
}

// The scaffolding echo needs a SOURCE that also carries it — a lesson
// merely mentioning anti-escalation language on its own stays clean, but
// echoing the goal's scaffolding quarantines (the db37d525 shape).
func TestScaffoldingEchoNeedsSource(t *testing.T) {
	lesson := "Do not escalate or stop merely because a linked page cannot be accessed"
	if got := Classify(lesson, "", ""); got != MintedFromOutcome {
		t.Fatalf("scaffolding without a source echo quarantined: %q", got)
	}
	goal := "Fetch the page. Do not escalate or stop merely because it fails."
	if got := Classify(lesson, goal, ""); got != MintedFromPrompt {
		t.Fatalf("goal-scaffolding echo not quarantined: %q", got)
	}
	if got := Classify(lesson, "", goal); got != MintedFromPrompt {
		t.Fatalf("evidence-scaffolding echo not quarantined: %q", got)
	}
}
