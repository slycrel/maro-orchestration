package judgment

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
)

// PromptVer is the llm arm's prompt template version. It is written into
// the prompt, so the recorded bytes say which template produced them, and
// the run fold re-derives them with the same constant. Bump it whenever
// the rendering below changes by a byte.
const PromptVer = "judgment-llm/1"

// LLM is the third provider: the engine's existing generative backend,
// asked the same typed Request in prose and parsed strictly. It exists
// because the seam must not be a special case for the new providers —
// whatever the seam replaces is refactored to fit through it too, so
// `llm` is a provider like any other and stays the default.
//
// It wraps a backend and passes its capabilities through UNCHANGED: a
// judge invocation made through this adapter records exactly the backend
// snapshot it recorded before the seam existed.
type LLM struct {
	B     invoke.Backend
	Model string // reported in the parsed Response; "" ⇒ the backend's model
	// Provider is the name this adapter answers to: `llm` (the engine's
	// own judge backend) or `hosted` (a cheap OpenAI-compatible tier).
	// Both render and parse identically; only the backend differs.
	Provider string
}

func (l *LLM) Name() string {
	if l.Provider != "" {
		return l.Provider
	}
	return ProviderLLM
}
func (l *LLM) Wire() bool { return false }

func (l *LLM) Capabilities() invoke.Capabilities { return l.B.Capabilities() }

func (l *LLM) Complete(ctx context.Context, req invoke.Request, sink invoke.Sink) (*invoke.Result, error) {
	return l.B.Complete(ctx, req, sink)
}

// NewHosted is the hosted provider: the same prose rendering and the
// same strict parse as `llm`, over a cheap OpenAI-compatible endpoint.
// baseURL, model and keyName default to the hosted-free tier when empty.
func NewHosted(baseURL, model, keyName string, key func(string) (string, error)) *LLM {
	if strings.TrimSpace(baseURL) == "" {
		baseURL = HostedBaseURL
	}
	if strings.TrimSpace(model) == "" {
		model = HostedModel
	}
	if strings.TrimSpace(keyName) == "" {
		keyName = HostedKeyName
	}
	name := keyName
	b := invoke.NewOpenAIChat(ProviderHosted, baseURL, model, keyName, func() (string, error) { return key(name) })
	return &LLM{B: b, Provider: ProviderHosted, Model: model}
}

// Render writes the Request as a deterministic prose prompt. Determinism
// is not a nicety: the fold re-derives this byte-for-byte.
func (l *LLM) Render(r Request) ([]byte, error) { return RenderPrompt(r) }

// RenderPrompt is Render as a package function, so the fold can re-derive
// a request without building a provider.
func RenderPrompt(r Request) ([]byte, error) {
	if err := r.Validate(); err != nil {
		return nil, err
	}
	var b strings.Builder
	fmt.Fprintf(&b, "maro judgment prompt %s\n\n", PromptVer)
	b.WriteString("You are answering typed questions about a state. Answer each question independently, from the state below and from nothing else.\n")
	b.WriteString("Reply with ONE JSON object and nothing else: one key per question id below, and no other keys.\n\n")
	b.WriteString("Answer shapes, by question type:\n")
	b.WriteString("- choice: {\"type\": \"choice\", \"choice\": \"<one option name>\", \"confidence\": <0..1>, \"probabilities\": {\"<option name>\": <0..1>, ...}, \"why\": \"<one sentence>\"}\n")
	b.WriteString("- noul: {\"type\": \"noul\", \"noul\": <0..1, the probability the statement is true>, \"why\": \"<one sentence>\"}\n")
	b.WriteString("- score: {\"type\": \"score\", \"score\": <0..N, the level index>, \"confidence\": <0..1>, \"probabilities\": {\"<level index>\": <0..1>, ...}, \"why\": \"<one sentence>\"}\n")
	b.WriteString("A question marked FALSIFIERS also carries \"falsifiers\": [\"<observation that would refute your answer>\", ...].\n")
	if err := renderState(&b, r.State); err != nil {
		return nil, err
	}
	b.WriteString("\n## Questions\n")
	for _, id := range r.Order {
		q := r.Questions[id]
		fmt.Fprintf(&b, "\n### %s (%s)\n%s\n", id, q.Type, q.Instructions)
		switch q.Type {
		case Choice:
			b.WriteString("Options:\n")
			for _, o := range q.Options {
				if o.Description == "" {
					fmt.Fprintf(&b, "- %s\n", o.Name)
				} else {
					fmt.Fprintf(&b, "- %s: %s\n", o.Name, o.Description)
				}
			}
		case Score:
			b.WriteString("Levels:\n")
			for i, lv := range q.Levels {
				fmt.Fprintf(&b, "- %d: %s\n", i, lv)
			}
		case Noul:
			if q.True != "" {
				fmt.Fprintf(&b, "1 means: %s\n0 means: %s\n", q.True, q.False)
			}
		}
		if q.Falsifiers {
			b.WriteString("FALSIFIERS: name what would prove this answer wrong.\n")
		}
	}
	return []byte(b.String()), nil
}

func renderState(b *strings.Builder, state any) error {
	b.WriteString("\n## State\n")
	switch v := state.(type) {
	case State:
		for _, s := range v.Sections {
			fmt.Fprintf(b, "\n### %s\n%s\n", s.Key, s.Text)
		}
	default:
		raw, err := encodeState(state)
		if err != nil {
			return err
		}
		var pretty bytes.Buffer
		if err := json.Indent(&pretty, raw, "", "  "); err != nil {
			return wireErr("state: %v", err)
		}
		b.WriteString("\n")
		b.Write(pretty.Bytes())
		b.WriteString("\n")
	}
	return nil
}

// Parse reads the model's one JSON object: {"<question id>": <answer>}.
// Strict — an unknown key, a missing typed value, an out-of-range number
// or a missing why is a refusal, never a guess. The caller records the
// refusal; nothing is invented to keep a verdict alive.
func (l *LLM) Parse(b []byte) (Response, error) {
	model := l.Model
	if model == "" {
		model = l.B.Capabilities().Model
	}
	return ParseAnswers(b, model)
}

// ParseAnswers is Parse as a package function (the fold re-derives with
// it, and the corpus replay reads an arm's raw answer with it).
func ParseAnswers(b []byte, model string) (Response, error) {
	body := unfence(b)
	var raw map[string]json.RawMessage
	if err := strictDecode(body, &raw); err != nil {
		return Response{}, err
	}
	if len(raw) == 0 {
		return Response{}, wireErr("no answers in the reply")
	}
	order, err := objectOrder(body)
	if err != nil {
		return Response{}, err
	}
	r := Response{Model: model, Answers: map[string]Answer{}, Order: order}
	for id, one := range raw {
		a, err := decodeAnswer(one)
		if err != nil {
			return Response{}, fmt.Errorf("answer %q: %w", id, err)
		}
		if strings.TrimSpace(a.Why) == "" {
			return Response{}, wireErr("answer %q carries no why (the llm arm's judgement always says why)", id)
		}
		r.Answers[id] = a
	}
	return r, nil
}

// unfence strips a ```json fence when the whole body is one (the CLIs
// fence a JSON answer often enough that refusing it would be a fight
// with the wrapper, not with the judgement).
func unfence(b []byte) []byte {
	s := strings.TrimSpace(string(b))
	if strings.HasPrefix(s, "```") {
		s = strings.TrimPrefix(s, "```json")
		s = strings.TrimPrefix(s, "```")
		s = strings.TrimSuffix(strings.TrimSpace(s), "```")
	}
	return []byte(strings.TrimSpace(s))
}
