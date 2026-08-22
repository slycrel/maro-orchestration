package llm

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func demoTools() []Tool {
	return []Tool{
		{Name: "complete_step", Description: "finish the step",
			Parameters: map[string]any{
				"type":       "object",
				"properties": map[string]any{"result": map[string]any{"type": "string"}},
			}},
		{Name: "flag_stuck", Description: "give up with a reason",
			Parameters: map[string]any{
				"type":       "object",
				"properties": map[string]any{"reason": map[string]any{"type": "string"}},
			}},
	}
}

func TestBuildPromptInjectsToolsBetweenSystemAndEndMarker(t *testing.T) {
	p := BuildPrompt([]Message{
		{Role: "system", Content: "sys"},
		{Role: "user", Content: "do it"},
	}, demoTools()...)
	iSys := strings.Index(p, "[SYSTEM INSTRUCTIONS]")
	iTools := strings.Index(p, "--- AVAILABLE TOOLS ---")
	iEndTools := strings.Index(p, "--- END TOOLS ---")
	iEnd := strings.Index(p, "[END SYSTEM INSTRUCTIONS]")
	if iSys < 0 || iTools < 0 || iEndTools < 0 || iEnd < 0 {
		t.Fatalf("missing prompt sections:\n%s", p)
	}
	if !(iSys < iTools && iTools < iEndTools && iEndTools < iEnd) {
		t.Fatalf("tool block not between system block and END marker:\n%s", p)
	}
	for _, want := range []string{
		`"complete_step": finish the step`,
		`"flag_stuck": give up with a reason`,
		// The load-bearing don't-execute-this line (Python LT-4 finding 10).
		"Never execute it",
	} {
		if !strings.Contains(p, want) {
			t.Errorf("prompt missing %q", want)
		}
	}
}

func TestBuildPromptWithoutToolsHasNoToolBlock(t *testing.T) {
	p := BuildPrompt([]Message{{Role: "user", Content: "classify this"}})
	if strings.Contains(p, "AVAILABLE TOOLS") {
		t.Fatalf("tool block leaked into a tool-less prompt:\n%s", p)
	}
}

func TestParseToolCallExtractsFromProse(t *testing.T) {
	tc := ParseToolCall(
		"Here is my answer:\n{\"tool\": \"complete_step\", \"result\": \"done work\", \"summary\": \"did it\"}",
		demoTools())
	if tc == nil || tc.Name != "complete_step" {
		t.Fatalf("expected complete_step, got %+v", tc)
	}
	if tc.Arguments["result"] != "done work" {
		t.Fatalf("arguments: %+v", tc.Arguments)
	}
	if _, hasTool := tc.Arguments["tool"]; hasTool {
		t.Fatalf("'tool' key must not leak into arguments: %+v", tc.Arguments)
	}
}

func TestParseToolCallRefusals(t *testing.T) {
	tools := demoTools()
	cases := map[string]string{
		"no JSON at all":       "just prose",
		"unknown tool":         `{"tool": "rm_rf", "path": "/"}`,
		"missing tool key":     `{"result": "x"}`,
		"non-string tool":      `{"tool": 42}`,
		"trailing second obj":  `{"tool": "complete_step", "result": "a"}{"tool": "flag_stuck"}`,
		"unbalanced fragments": "prefix { not json } suffix",
	}
	for name, text := range cases {
		if tc := ParseToolCall(text, tools); tc != nil {
			t.Errorf("%s: expected nil, got %+v", name, tc)
		}
	}
}

func TestParseToolCallKeepsLargeIntegersExact(t *testing.T) {
	// UseNumber: a 2^53+1-scale argument must survive verbatim, not
	// round through float64 (the pack tranche's r3 lesson, same class).
	tc := ParseToolCall(`{"tool": "complete_step", "result": "ok", "id": 9007199254740993}`,
		demoTools())
	if tc == nil {
		t.Fatal("parse failed")
	}
	n, ok := tc.Arguments["id"].(json.Number)
	if !ok || n.String() != "9007199254740993" {
		t.Fatalf("large int mangled: %#v", tc.Arguments["id"])
	}
}

func TestBuildArgsEnvUtilityLane(t *testing.T) {
	args, env := buildArgsEnv(Options{})
	joined := strings.Join(args, " ")
	if !strings.Contains(joined, "--tools") {
		t.Fatalf("utility lane must disable tools: %v", args)
	}
	if strings.Contains(joined, "--disallowedTools") {
		t.Fatalf("utility lane must not carry the executor deny-list: %v", args)
	}
	found := false
	for _, o := range env {
		if o.Key == "CLAUDE_CODE_MAX_OUTPUT_TOKENS" && o.Set && o.Value == "16000" {
			found = true
		}
	}
	if !found {
		t.Fatalf("utility lane missing output ceiling: %+v", env)
	}
}

func TestBuildArgsEnvExecutorLane(t *testing.T) {
	t.Setenv("MARO_BASH_MAX_OUTPUT_CHARS", "12345")
	args, env := buildArgsEnv(Options{AgentTools: true})
	joined := strings.Join(args, " ")
	if strings.Contains(joined, "--tools") {
		t.Fatalf("executor lane must NOT disable the tool set: %v", args)
	}
	if !strings.Contains(joined, "--disallowedTools") ||
		!strings.Contains(joined, "WebFetch,WebSearch") {
		t.Fatalf("executor lane missing WebFetch/WebSearch deny: %v", args)
	}
	for _, o := range env {
		if o.Key == "CLAUDE_CODE_MAX_OUTPUT_TOKENS" {
			t.Fatalf("executor lane must not ceiling output tokens: %+v", env)
		}
	}
	found := false
	for _, o := range env {
		if o.Key == "BASH_MAX_OUTPUT_LENGTH" && o.Set && o.Value == "12345" {
			found = true
		}
	}
	if !found {
		t.Fatalf("executor lane missing bash cap: %+v", env)
	}
}

func TestBashOutputCapEnvPrecedence(t *testing.T) {
	// Explicit disable means UNSET IN CHILD, even beside an exported
	// operator value (Python adversarial review 2026-07-27: the disable
	// silently did nothing).
	t.Setenv("MARO_BASH_MAX_OUTPUT_CHARS", "0")
	t.Setenv("BASH_MAX_OUTPUT_LENGTH", "50000")
	env := bashOutputCapEnv()
	if len(env) != 1 || env[0].Key != "BASH_MAX_OUTPUT_LENGTH" || env[0].Set {
		t.Fatalf("explicit disable must unset the inherited cap: %+v", env)
	}

	// Non-int MARO value falls through; operator's own env wins (inherit
	// as-is → nil, no opinion).
	t.Setenv("MARO_BASH_MAX_OUTPUT_CHARS", "banana")
	if env := bashOutputCapEnv(); env != nil {
		t.Fatalf("operator-governed cap must be inherited untouched: %+v", env)
	}

	// Neither set: default cap applies. (config default — the test env
	// has no user config overriding llm.subprocess.bash_max_output_chars;
	// MARO_USER_DIR pins config reads to an empty tmp dir to make sure.)
	t.Setenv("MARO_USER_DIR", t.TempDir())
	unsetEnv(t, "MARO_BASH_MAX_OUTPUT_CHARS")
	unsetEnv(t, "BASH_MAX_OUTPUT_LENGTH")
	env = bashOutputCapEnv()
	if len(env) != 1 || !env[0].Set || env[0].Value != "24000" {
		t.Fatalf("default cap expected: %+v", env)
	}
}

// unsetEnv removes a var for the test's duration: t.Setenv registers the
// restore of the original value, then Unsetenv leaves it truly absent
// (Setenv("") alone would read as present-and-empty via LookupEnv).
func unsetEnv(t *testing.T, key string) {
	t.Helper()
	t.Setenv(key, "")
	os.Unsetenv(key)
}

func TestChildEnvUnsetAndOverride(t *testing.T) {
	t.Setenv("MARO_TEST_KEEP", "yes")
	t.Setenv("MARO_TEST_DROP", "secret")
	env := childEnv([]envOverride{
		{Key: "MARO_TEST_DROP", Set: false},
		{Key: "MARO_TEST_ADD", Value: "v1", Set: true},
	})
	joined := strings.Join(env, "\n")
	if !strings.Contains(joined, "MARO_TEST_KEEP=yes") {
		t.Fatal("inherited var lost")
	}
	if strings.Contains(joined, "MARO_TEST_DROP=") {
		t.Fatal("unset override did not remove the key")
	}
	if !strings.Contains(joined, "MARO_TEST_ADD=v1") {
		t.Fatal("set override missing")
	}
}
