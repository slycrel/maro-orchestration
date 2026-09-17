package judgment

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

// the exact body and answer the live TypeSafe API took and gave
// (2026-09-17 smoke), kept as the wire fixture this codec is held to.
const liveResponse = `{"model":"jev-1.13.0","answers":{"verdict":{"type":"choice","choice":"done","confidence":0.99,"probabilities":{"blocked":0.01,"unclear":0.0,"done":0.99}},"is_correct":{"type":"noul","noul":0.96},"quality":{"type":"score","score":1.91,"confidence":0.86,"legend":{"0":"broken","1":"works but sloppy","2":"clean and correct"},"probabilities":{"0":0.01,"1":0.08,"2":0.91}}},"usage":{"input_tokens":473,"output_tokens":71}}`

func stepQ() Question {
	return Question{Type: Choice, Instructions: "Is this step done?", Options: []Option{
		{Name: "done", Description: "the step's own part is done"},
		{Name: "blocked", Description: "it could not be done"},
		{Name: "unclear", Description: "cannot tell"},
	}}
}

func TestEncodeRequestIsTheWireShapeAndDeterministic(t *testing.T) {
	req := Request{Model: "jev-latest", State: Sect("goal", "g", "result", "r"),
		Questions: map[string]Question{
			"verdict":    stepQ(),
			"is_correct": {Type: Noul, Instructions: "Is it correct?"},
			"quality":    {Type: Score, Instructions: "How good?", Levels: []string{"broken", "sloppy", "clean"}},
		},
		Order: []string{"verdict", "is_correct", "quality"}}
	b, err := EncodeRequest(req)
	if err != nil {
		t.Fatal(err)
	}
	want := `{"model":"jev-latest","state":{"goal":"g","result":"r"},"questions":{` +
		`"verdict":{"type":"choice","instructions":"Is this step done?","criteria":{"done":"the step's own part is done","blocked":"it could not be done","unclear":"cannot tell"}},` +
		`"is_correct":{"type":"noul","instructions":"Is it correct?"},` +
		`"quality":{"type":"score","instructions":"How good?","criteria":["broken","sloppy","clean"]}}}`
	if string(b) != want {
		t.Fatalf("wire body:\n got %s\nwant %s", b, want)
	}
	if !json.Valid(b) {
		t.Fatal("the body is not valid JSON")
	}
	// determinism: the same request, again, and a decoded round trip
	again, _ := EncodeRequest(req)
	if string(again) != string(b) {
		t.Fatal("encoding is not deterministic")
	}
	back, err := DecodeRequest(b)
	if err != nil {
		t.Fatal(err)
	}
	rt, err := EncodeRequest(back)
	if err != nil {
		t.Fatal(err)
	}
	if string(rt) != string(b) {
		t.Fatalf("round trip changed the bytes:\n got %s\nwant %s", rt, b)
	}
}

func TestOptionWithoutADescriptionIsNull(t *testing.T) {
	b, err := EncodeRequest(Ask1("m", Sect("s", "x"), "q", Question{Type: Choice, Instructions: "i",
		Options: []Option{{Name: "a"}, {Name: "b", Description: "bee"}}}))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), `"criteria":{"a":null,"b":"bee"}`) {
		t.Fatalf("no null description: %s", b)
	}
}

func TestDecodeResponseReadsTheLiveShape(t *testing.T) {
	r, err := DecodeResponse([]byte(liveResponse))
	if err != nil {
		t.Fatal(err)
	}
	if r.Model != "jev-1.13.0" || r.Usage.InputTokens != 473 || r.Usage.OutputTokens != 71 {
		t.Fatalf("envelope: %+v", r)
	}
	if a := r.Answers["verdict"]; a.Type != Choice || a.Choice != "done" || a.Confidence != 0.99 || a.Probabilities["done"] != 0.99 {
		t.Fatalf("choice answer: %+v", a)
	}
	if a := r.Answers["is_correct"]; a.Type != Noul || a.Noul != 0.96 {
		t.Fatalf("noul answer: %+v", a)
	}
	if a := r.Answers["quality"]; a.Type != Score || a.Score != 1.91 || a.Legend["2"] != "clean and correct" {
		t.Fatalf("score answer: %+v", a)
	}
	if got := len(r.Order); got != 3 || r.Order[0] != "verdict" {
		t.Fatalf("order not preserved: %v", r.Order)
	}
}

func TestRefusals(t *testing.T) {
	q := stepQ()
	cases := []struct {
		name string
		run  func() error
	}{
		{"question type out of vocabulary", func() error {
			_, err := EncodeRequest(Ask1("m", Sect("a", "b"), "q", Question{Type: "vibes", Instructions: "i"}))
			return err
		}},
		{"choice with one option", func() error {
			_, err := EncodeRequest(Ask1("m", Sect("a", "b"), "q", Question{Type: Choice, Instructions: "i", Options: []Option{{Name: "only"}}}))
			return err
		}},
		{"score with one level", func() error {
			_, err := EncodeRequest(Ask1("m", Sect("a", "b"), "q", Question{Type: Score, Instructions: "i", Levels: []string{"one"}}))
			return err
		}},
		{"noul with half its criteria", func() error {
			_, err := EncodeRequest(Ask1("m", Sect("a", "b"), "q", Question{Type: Noul, Instructions: "i", True: "yes"}))
			return err
		}},
		{"no state", func() error {
			_, err := EncodeRequest(Ask1("m", nil, "q", q))
			return err
		}},
		{"no model", func() error {
			_, err := EncodeRequest(Ask1("", Sect("a", "b"), "q", q))
			return err
		}},
		{"answer of the wrong type", func() error {
			return Answer{Type: Noul, Noul: 0.5}.Validate(q)
		}},
		{"choice outside the vocabulary", func() error {
			return Answer{Type: Choice, Choice: "maybe", Confidence: 0.5}.Validate(q)
		}},
		{"confidence out of range", func() error {
			return Answer{Type: Choice, Choice: "done", Confidence: 7}.Validate(q)
		}},
		{"probability for an option that does not exist", func() error {
			return Answer{Type: Choice, Choice: "done", Confidence: 0.5, Probabilities: map[string]float64{"nope": 0.5}}.Validate(q)
		}},
		{"score above the ladder", func() error {
			return Answer{Type: Score, Score: 9, Confidence: 0.5}.Validate(Question{Type: Score, Instructions: "i", Levels: []string{"a", "b"}})
		}},
		{"unknown key in an answer", func() error {
			_, err := DecodeResponse([]byte(`{"model":"m","answers":{"q":{"type":"noul","noul":0.5,"vibe":1}},"usage":{"input_tokens":1,"output_tokens":1}}`))
			return err
		}},
		{"noul answer without its probability", func() error {
			_, err := DecodeResponse([]byte(`{"model":"m","answers":{"q":{"type":"noul"}},"usage":{"input_tokens":1,"output_tokens":1}}`))
			return err
		}},
		{"choice answer without its confidence", func() error {
			_, err := DecodeResponse([]byte(`{"model":"m","answers":{"q":{"type":"choice","choice":"done"}},"usage":{"input_tokens":1,"output_tokens":1}}`))
			return err
		}},
		{"trailing content", func() error {
			_, err := DecodeResponse([]byte(`{"model":"m","answers":{},"usage":{"input_tokens":0,"output_tokens":0}} and then some`))
			return err
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			err := c.run()
			if err == nil {
				t.Fatal("accepted what it must refuse")
			}
			if !errors.Is(err, ErrWire) {
				t.Fatalf("not a wire refusal: %v", err)
			}
		})
	}
}

func TestResponseMustAnswerExactlyWhatWasAsked(t *testing.T) {
	req := Ask1("m", Sect("goal", "g"), "outcome", stepQ())
	ok := Response{Answers: map[string]Answer{"outcome": {Type: Choice, Choice: "done", Confidence: 0.9}}}
	if err := ok.Validate(req); err != nil {
		t.Fatal(err)
	}
	missing := Response{Answers: map[string]Answer{}}
	if err := missing.Validate(req); err == nil {
		t.Fatal("a response with no answer passed")
	}
	extra := Response{Answers: map[string]Answer{
		"outcome": {Type: Choice, Choice: "done", Confidence: 0.9},
		"bonus":   {Type: Noul, Noul: 0.5},
	}}
	if err := extra.Validate(req); err == nil {
		t.Fatal("a response answering an unasked question passed")
	}
}

func TestLLMPromptIsDeterministicAndVersioned(t *testing.T) {
	req := Ask1("m", Sect("goal", "g", "step", "s", "result", "r"), "outcome", func() Question {
		q := stepQ()
		q.Falsifiers = true
		return q
	}())
	a, err := RenderPrompt(req)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := RenderPrompt(req)
	if string(a) != string(b) {
		t.Fatal("the llm rendering is not deterministic")
	}
	for _, want := range []string{PromptVer, "## State", "### goal", "### outcome (choice)", "- done: the step's own part is done", "FALSIFIERS"} {
		if !strings.Contains(string(a), want) {
			t.Fatalf("prompt missing %q:\n%s", want, a)
		}
	}
}

func TestLLMParseIsStrict(t *testing.T) {
	good := `{"outcome": {"type": "choice", "choice": "done", "confidence": 0.9, "probabilities": {"done": 0.9, "blocked": 0.05, "unclear": 0.05}, "why": "the step's result matches the step"}}`
	r, err := ParseAnswers([]byte("```json\n"+good+"\n```"), "claude")
	if err != nil {
		t.Fatal(err)
	}
	a, err := r.One()
	if err != nil {
		t.Fatal(err)
	}
	if a.Choice != "done" || a.Why == "" || a.Probabilities["done"] != 0.9 {
		t.Fatalf("answer: %+v", a)
	}
	if err := r.Validate(Ask1("m", Sect("goal", "g"), "outcome", stepQ())); err != nil {
		t.Fatal(err)
	}
	for _, bad := range []string{
		`{"outcome": {"type": "choice", "choice": "done", "confidence": 0.9}}`,                     // no why
		`{"outcome": {"type": "choice", "choice": "done", "confidence": 0.9, "why": "x"}} junk`,    // trailing
		`{"outcome": {"type": "choice", "choice": "done", "confidence": 0.9, "why": "x", "z": 1}}`, // unknown key
		`not json at all`,
		`{}`,
	} {
		if _, err := ParseAnswers([]byte(bad), "claude"); err == nil {
			t.Fatalf("accepted %q", bad)
		}
	}
}

func TestEncodeResponseRoundTrips(t *testing.T) {
	r, err := DecodeResponse([]byte(liveResponse))
	if err != nil {
		t.Fatal(err)
	}
	b, err := EncodeResponse(r)
	if err != nil {
		t.Fatal(err)
	}
	again, err := DecodeResponse(b)
	if err != nil {
		t.Fatalf("re-encoded response does not decode: %v\n%s", err, b)
	}
	if again.Answers["quality"].Score != 1.91 || again.Usage.InputTokens != 473 {
		t.Fatalf("round trip lost content: %s", b)
	}
}
