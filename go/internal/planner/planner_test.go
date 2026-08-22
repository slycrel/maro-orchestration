package planner

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

func TestDecomposeParsesFencedReply(t *testing.T) {
	fake := &llm.Fake{Script: []string{"```json\n[\"a\", \"b\", \"c\"]\n```"}}
	steps, _, err := Decompose(context.Background(), fake, t.TempDir(), "do the thing", 8)
	if err != nil || len(steps) != 3 {
		t.Fatalf("steps=%v err=%v", steps, err)
	}
}

func TestDecomposeCapsAtMaxSteps(t *testing.T) {
	fake := &llm.Fake{Script: []string{`["1","2","3","4","5"]`}}
	steps, _, err := Decompose(context.Background(), fake, t.TempDir(), "goal", 2)
	if err != nil || len(steps) != 2 {
		t.Fatalf("steps=%v err=%v", steps, err)
	}
}

func TestDecomposeErrorsOnEmptyAndGarbage(t *testing.T) {
	ws := t.TempDir()
	if _, _, err := Decompose(context.Background(), &llm.Fake{Script: []string{"x"}}, ws, "  ", 8); err == nil {
		t.Fatal("empty goal must error")
	}
	if _, _, err := Decompose(context.Background(), &llm.Fake{Script: []string{`["  ", ""]`}}, ws, "goal", 8); err == nil {
		t.Fatal("all-blank steps must error")
	}
}

// The caps-sweep fix carries over: a long hand-written operator doc rides
// the decompose prompt whole; a runaway one is bounded WITH the marker.
func TestOperatorDocsRideWholeWithMarkedRunawayBound(t *testing.T) {
	ws := t.TempDir()
	userDir := filepath.Join(ws, "user")
	if err := os.MkdirAll(userDir, 0o755); err != nil {
		t.Fatal(err)
	}
	body := "GOALS-HEAD " + strings.Repeat("g", 1400) + " GOALS-TAIL"
	if err := os.WriteFile(filepath.Join(userDir, "GOALS.md"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(userDir, "SIGNALS.md"),
		[]byte(strings.Repeat("s", 6000)), 0o644); err != nil {
		t.Fatal(err)
	}

	fake := &llm.Fake{Script: []string{`["one"]`}}
	if _, _, err := Decompose(context.Background(), fake, ws, "goal", 4); err != nil {
		t.Fatal(err)
	}
	prompt := fake.Prompts[0]
	if !strings.Contains(prompt, "GOALS-TAIL") {
		t.Fatal("typical-length operator doc was cut (the [:500] starvation, reborn)")
	}
	if !strings.Contains(prompt, "USER CONTEXT (GOALS.md)") {
		t.Fatal("operator doc header missing")
	}
	if strings.Contains(prompt, strings.Repeat("s", 6000)) {
		t.Fatal("runaway doc reached the prompt unbounded")
	}
	if !strings.Contains(prompt, "[truncated: first 4000 of 6000 characters]") {
		t.Fatal("runaway doc cut without marker")
	}
}
