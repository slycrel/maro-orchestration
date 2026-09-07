package run

import (
	"strings"
	"testing"
)

// The engine must know its own name: operators phrase goals as "ask maro to
// …", and an intake that does not know it IS maro asks who maro is (shadow
// pair 80a5a0dc, 2026-09-07: three of three Go challengers ended on that
// question while the Python arm, whose Director prompt names Maro, ran).
func TestPromptsNameTheEngine(t *testing.T) {
	for name, p := range map[string][]byte{
		"intent": intentPrompt([]byte("g"), nil),
		"plan":   planPrompt([]byte("g"), "i", nil, nil),
		"step":   stepPrompt([]byte("g"), []string{"s"}, 1, nil, nil),
	} {
		if !strings.Contains(string(p), "named Maro") || !strings.Contains(string(p), `"maro"`) {
			t.Fatalf("%s prompt does not name the engine: %.120s", name, p)
		}
	}
}
