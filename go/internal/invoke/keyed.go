package invoke

import (
	"context"
	"strings"
	"sync"
)

// Rule is one entry of a Keyed backend: the first rule whose key matches
// the prompt answers it. A prefix rule matches the prompt's start, a plain
// rule any substring — so concurrent children get deterministic responses
// whatever their order and however much of the plan a prompt quotes. A
// blocking rule holds the call until released or cancelled; an effect rule
// reports a tool effect first.
type Rule struct {
	Key    string
	Answer string
	Prefix bool
	Block  chan struct{}
	Effect *ScriptedEffect
}

func (r Rule) matches(prompt string) bool {
	if r.Prefix {
		return strings.HasPrefix(prompt, r.Key)
	}
	return strings.Contains(prompt, r.Key)
}

// Keyed is a test backend that answers by ordered rules over the prompt,
// falling back to Def. It records every request it saw and meters usage
// as a running call count so receipts are distinct and nonzero.
type Keyed struct {
	Caps  Capabilities
	Rules []Rule
	Def   string
	mu    sync.Mutex
	Seen  []Request
	usage int64
}

func (k *Keyed) Capabilities() Capabilities { return k.Caps }

func (k *Keyed) Complete(ctx context.Context, req Request, sink Sink) (*Result, error) {
	k.mu.Lock()
	k.Seen = append(k.Seen, req)
	k.usage++
	u := k.usage
	k.mu.Unlock()
	prompt := string(req.Prompt)
	for _, r := range k.Rules {
		if !r.matches(prompt) {
			continue
		}
		if r.Block != nil {
			select {
			case <-r.Block:
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}
		if r.Effect != nil {
			if _, _, err := sink.Observe(EffectEvent{Op: r.Effect.Op, Input: r.Effect.Input}); err != nil {
				return nil, err
			}
		}
		if r.Answer != "" {
			return &Result{Response: []byte(r.Answer), Terminal: TerminalComplete, Usage: Usage{InputTokens: u}}, nil
		}
	}
	return &Result{Response: []byte(k.Def), Terminal: TerminalComplete, Usage: Usage{InputTokens: u}}, nil
}

// Calls is how many requests the backend has seen.
func (k *Keyed) Calls() int {
	k.mu.Lock()
	defer k.mu.Unlock()
	return len(k.Seen)
}
