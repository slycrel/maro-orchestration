package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestMergeWorkspaceWinsNestedOneLevel(t *testing.T) {
	base := map[string]any{
		"model": "sonnet",
		"inspector": map[string]any{
			"breach_threshold": 0.30,
			"keep_me":          true,
		},
	}
	over := map[string]any{
		"inspector": map[string]any{"breach_threshold": 0.50},
		"extra":     1,
	}
	m := Merge(base, over)
	if got := Get(m, "inspector.breach_threshold", 0.0); got != 0.50 {
		t.Fatalf("workspace override lost: %v", got)
	}
	if got := Get(m, "inspector.keep_me", false); got != true {
		t.Fatalf("one-level merge dropped sibling key")
	}
	if got := Get(m, "model", ""); got != "sonnet" {
		t.Fatalf("base scalar lost: %v", got)
	}
}

func TestGetFallsBackOnMissingAndMistyped(t *testing.T) {
	m := map[string]any{"a": map[string]any{"b": "text"}}
	if got := Get(m, "a.missing", "def"); got != "def" {
		t.Fatalf("missing key: %v", got)
	}
	if got := Get(m, "a.b", 7); got != 7 {
		t.Fatalf("type mismatch must fall back: %v", got)
	}
	if got := Get(m, "a.b.too.deep", "def"); got != "def" {
		t.Fatalf("descending through a scalar: %v", got)
	}
}

func TestGetIntTolerantFloatLookup(t *testing.T) {
	m := map[string]any{"n": 3} // YAML integers decode as int
	if got := Get(m, "n", 0.0); got != 3.0 {
		t.Fatalf("int-to-float lookup: %v", got)
	}
}

func TestWorkspaceHonorsMaroWorkspaceEnv(t *testing.T) {
	t.Setenv("MARO_WORKSPACE", "/tmp/x-ws")
	if got := Workspace(); got != "/tmp/x-ws" {
		t.Fatalf("MARO_WORKSPACE ignored: %v", got)
	}
}

func TestLoadReportsUnparseableFileInsteadOfSwallowing(t *testing.T) {
	home := t.TempDir()
	ws := filepath.Join(home, "workspace")
	if err := os.MkdirAll(ws, 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("MARO_HOME", home)
	t.Setenv("MARO_WORKSPACE", ws)
	if err := os.WriteFile(filepath.Join(ws, "config.yml"),
		[]byte(":\tnot yaml ["), 0o644); err != nil {
		t.Fatal(err)
	}
	_, warnings := Load()
	if len(warnings) == 0 {
		t.Fatal("unparseable config produced no warning — silent swallow")
	}
}
