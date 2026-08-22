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
