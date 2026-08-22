// Package planner ports decompose — goal in, ordered concrete steps out.
package planner

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

const decomposeSystem = `You are a planning assistant for an autonomous agent.
Break the user's goal into concrete, sequential, independently executable
steps. Each step is one clear instruction a capable worker can act on
without asking questions. Prefer fewer, meatier steps over many trivial
ones.

Respond ONLY with a JSON array of step strings — no prose, no markdown
fence, no numbering inside the strings.`

// Decompose asks the adapter to break goal into at most maxSteps steps.
// Operator context (GOALS/CONTEXT/SIGNALS.md from the workspace user
// overlay) rides the prompt whole under the OperatorDoc budget — the
// port inherits the caps-sweep fix, not the [:500] starvation it
// replaced.
func Decompose(ctx context.Context, a llm.Adapter, goal string, maxSteps int) ([]string, error) {
	if strings.TrimSpace(goal) == "" {
		return nil, fmt.Errorf("empty goal")
	}
	if maxSteps <= 0 {
		maxSteps = 8
	}

	var sb strings.Builder
	fmt.Fprintf(&sb, "Goal: %s\n\nBreak this into at most %d steps.", goal, maxSteps)
	for _, doc := range operatorDocs() {
		fmt.Fprintf(&sb, "\n\nUSER CONTEXT (%s):\n%s", doc.name, doc.body)
	}

	resp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: decomposeSystem},
		{Role: "user", Content: sb.String()},
	}, llm.Options{MaxTokens: 1024, Temperature: 0.2, Purpose: "decompose"})
	if err != nil {
		return nil, fmt.Errorf("decompose: %w", err)
	}

	steps, err := jsonx.StringArray(resp.Content)
	if err != nil {
		return nil, fmt.Errorf("decompose reply: %w", err)
	}
	var out []string
	for _, s := range steps {
		if t := strings.TrimSpace(s); t != "" {
			out = append(out, t)
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("decompose produced no usable steps")
	}
	if len(out) > maxSteps {
		out = out[:maxSteps]
	}
	return out, nil
}

type opDoc struct{ name, body string }

// operatorDocs reads the workspace user overlay only — the repo-template
// fallback the Python planner has is deliberately absent here until the
// port ships install templates of its own (a fresh Go checkout has no
// repo user/ dir to fall back to).
func operatorDocs() []opDoc {
	var out []opDoc
	userDir := filepath.Join(config.Workspace(), "user")
	for _, name := range []string{"GOALS.md", "CONTEXT.md", "SIGNALS.md"} {
		raw, err := os.ReadFile(filepath.Join(userDir, name))
		if err != nil {
			continue // absent overlay file is the normal fresh state
		}
		body := strings.TrimSpace(string(raw))
		if body == "" {
			continue
		}
		out = append(out, opDoc{name: name, body: budget.OperatorDoc.Clip(body)})
	}
	return out
}
