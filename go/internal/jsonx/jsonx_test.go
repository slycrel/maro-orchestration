package jsonx

import "testing"

func TestStringArrayPlain(t *testing.T) {
	got, err := StringArray(`["a", "b"]`)
	if err != nil || len(got) != 2 || got[0] != "a" {
		t.Fatalf("got %v err %v", got, err)
	}
}

func TestStringArrayFencedWithProse(t *testing.T) {
	text := "Sure! Here are the steps:\n```json\n[\"first step\", \"second step\"]\n```\nLet me know."
	got, err := StringArray(text)
	if err != nil || len(got) != 2 || got[1] != "second step" {
		t.Fatalf("got %v err %v", got, err)
	}
}

func TestStringArrayBracketInsideStringDoesNotEndCarve(t *testing.T) {
	got, err := StringArray(`["use x[0] to index", "done"]`)
	if err != nil || len(got) != 2 || got[0] != "use x[0] to index" {
		t.Fatalf("got %v err %v", got, err)
	}
}

func TestStringArrayEscapedQuoteInsideString(t *testing.T) {
	got, err := StringArray(`["say \"hi\" politely"]`)
	if err != nil || len(got) != 1 || got[0] != `say "hi" politely` {
		t.Fatalf("got %v err %v", got, err)
	}
}

func TestStringArrayRejectsNonStringElements(t *testing.T) {
	if _, err := StringArray(`["ok", 42]`); err == nil {
		t.Fatal("non-string element must error, not coerce")
	}
}

func TestStringArrayNoArrayErrors(t *testing.T) {
	if _, err := StringArray("no json here"); err == nil {
		t.Fatal("absent array must error")
	}
}

func TestStringArrayUnbalancedErrors(t *testing.T) {
	if _, err := StringArray(`["a", "b"`); err == nil {
		t.Fatal("unbalanced array must error")
	}
}

func TestObjectFenced(t *testing.T) {
	got, err := Object("```json\n{\"on_course\": true, \"note\": \"has } inside\"}\n```")
	if err != nil {
		t.Fatal(err)
	}
	if got["on_course"] != true {
		t.Fatalf("got %v", got)
	}
}

// A stray bracket in prose ahead of the fenced answer must not misdirect
// the carve (adversarial round 2026-08-22; the Python sibling shares the
// no-fence weakness, so the fence path is where Go can be strictly better).
func TestStringArrayStrayBracketBeforeFence(t *testing.T) {
	text := "See the docs [here](url) for context.\n```json\n[\"step one\", \"step two\"]\n```"
	got, err := StringArray(text)
	if err != nil || len(got) != 2 || got[0] != "step one" {
		t.Fatalf("got %v err %v", got, err)
	}
}

// Ported llm_parse.strip_think_blocks behavior: hypothetical JSON inside
// a reasoning trace must not be mistaken for the answer.
func TestObjectIgnoresJSONInsideThinkBlock(t *testing.T) {
	text := "<think>maybe {\"passed\": true}? no...</think>\n{\"passed\": false}"
	got, err := Object(text)
	if err != nil {
		t.Fatal(err)
	}
	if got["passed"] != false {
		t.Fatalf("carved the think-block's hypothetical JSON: %v", got)
	}
}

// An unclosed think trace (token budget cut it off) has no answer to
// keep — the extraction must error, not scavenge from the trace.
func TestObjectUnclosedThinkBlockErrors(t *testing.T) {
	if _, err := Object("<think>drafting {\"passed\": true} but"); err == nil {
		t.Fatal("unclosed think block must yield no payload")
	}
}
