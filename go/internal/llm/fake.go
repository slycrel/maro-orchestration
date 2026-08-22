package llm

import (
	"context"
	"fmt"
	"sync"
)

// Fake is a scripted adapter for tests and --dry-run. Responses are
// consumed in order; when the script runs out it repeats the last entry
// (a loop probing N steps shouldn't need N+1 lines of script for the
// common "every step says done" case).
type Fake struct {
	Script []string

	mu    sync.Mutex
	calls int
	// Prompts records every flattened prompt for assertions.
	Prompts []string
}

func (f *Fake) Name() string { return "fake" }

func (f *Fake) Complete(_ context.Context, msgs []Message, opts Options) (*Response, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Prompts = append(f.Prompts, BuildPrompt(msgs))
	if len(f.Script) == 0 {
		return nil, fmt.Errorf("fake adapter with empty script (purpose=%s)", opts.Purpose)
	}
	i := f.calls
	if i >= len(f.Script) {
		i = len(f.Script) - 1
	}
	f.calls++
	return &Response{Content: f.Script[i], TokensIn: 10, TokensOut: 5}, nil
}
