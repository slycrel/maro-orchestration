package scrub

import (
	"strings"
	"testing"
)

func TestSecretsRedactsAllPortedShapes(t *testing.T) {
	cases := []string{
		"key sk-ant-abcdefghijklmnop1234 inline",
		"key sk-abcdefghijklmnop1234 inline",
		"tok ghp_abcdefghijklmnopqrst inline",
		"slack xoxb-1234567890-abc inline",
		"aws AKIAABCDEFGHIJKLMNOP inline",
		"password: hunter2hunter2",
		"Api_Key = supersecretvalue",
	}
	for _, c := range cases {
		got := Secrets(c)
		if !strings.Contains(got, "[REDACTED]") {
			t.Errorf("shape survived: %q -> %q", c, got)
		}
	}
	benign := "the word token appears without a value nearby"
	if Secrets(benign) != benign {
		t.Errorf("benign text mangled: %q", Secrets(benign))
	}
}

// $HOME must be replaced before the bare username would chew into it
// (longest-needle-first, ported ordering).
func TestIdentifiersHomeBeforeUsername(t *testing.T) {
	id := BuildIdentifiers("/home/clawd", "minibox", []string{"someone@example.com"})
	got := id.Apply("ran from /home/clawd/claude as clawd on minibox for someone@example.com")
	want := "ran from [HOME]/claude as [USER] on [HOST] for [REDACTED]"
	if got != want {
		t.Fatalf("got %q want %q", got, want)
	}
}

// Word bounds: a username that is a substring of an ordinary word must
// not fire.
func TestIdentifiersWordBounded(t *testing.T) {
	id := BuildIdentifiers("/home/ted", "h", nil)
	if got := id.Apply("attempted and repeated"); got != "attempted and repeated" {
		t.Fatalf("substring over-redaction: %q", got)
	}
	if got := id.Apply("user ted logged in"); got != "user [USER] logged in" {
		t.Fatalf("bounded match missed: %q", got)
	}
}

// The recursive walk must reach nested values AND keys.
func TestWalkReachesNestedStrings(t *testing.T) {
	v := map[string]any{
		"outer": []any{map[string]any{"sk-ant-abcdefghijklmnop1234": "val",
			"deep": "ghp_abcdefghijklmnopqrst"}},
	}
	out := Walk(v, Secrets).(map[string]any)
	inner := out["outer"].([]any)[0].(map[string]any)
	if _, ok := inner["[REDACTED]"]; !ok {
		t.Fatalf("key not scrubbed: %v", inner)
	}
	if inner["deep"] != "[REDACTED]" {
		t.Fatalf("nested value not scrubbed: %v", inner)
	}
}
