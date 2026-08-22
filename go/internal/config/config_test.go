package config

import (
	"os"
	"path/filepath"
	"strings"
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

func TestGetFloatTolerantIntLookup(t *testing.T) {
	// The reverse direction (adversarial round 2026-08-22): an operator
	// writing `max_steps: 8.0` must still get their 8, not the default.
	m := map[string]any{"n": 8.0, "frac": 8.5}
	if got := Get(m, "n", 3); got != 8 {
		t.Fatalf("float-to-int lookup discarded the override: %v", got)
	}
	if got := Get(m, "frac", 3); got != 3 {
		t.Fatalf("non-integral float must fall back, not round: %v", got)
	}
}

func TestWorkspaceHonorsMaroWorkspaceEnv(t *testing.T) {
	t.Setenv("MARO_WORKSPACE", "/tmp/x-ws")
	if got := Workspace(); got != "/tmp/x-ws" {
		t.Fatalf("MARO_WORKSPACE ignored: %v", got)
	}
}

// The 2026-08-16 live-ledger incident, pinned: MARO_HOME is NOT a
// variable this system reads — Python config.workspace_root never did,
// and the Go port must not either (adversarial round 2026-08-22,
// Architect: v0 shipped with MARO_HOME steering the workspace).
func TestWorkspaceIgnoresMaroHome(t *testing.T) {
	t.Setenv("MARO_WORKSPACE", "")
	t.Setenv("OPENCLAW_WORKSPACE", "")
	t.Setenv("WORKSPACE_ROOT", "")
	t.Setenv("MARO_HOME", "/tmp/should-not-matter")
	got := Workspace()
	if strings.HasPrefix(got, "/tmp/should-not-matter") {
		t.Fatalf("MARO_HOME moved the workspace: %v", got)
	}
	home, _ := os.UserHomeDir()
	if got != filepath.Join(home, ".maro", "workspace") {
		t.Fatalf("default workspace is not ~/.maro/workspace: %v", got)
	}
}

// Legacy compat names, in Python's exact priority order.
func TestWorkspaceCompatVarsInPythonOrder(t *testing.T) {
	t.Setenv("MARO_WORKSPACE", "")
	t.Setenv("OPENCLAW_WORKSPACE", "/tmp/openclaw-ws")
	t.Setenv("WORKSPACE_ROOT", "/tmp/root-ws")
	if got := Workspace(); got != "/tmp/openclaw-ws" {
		t.Fatalf("OPENCLAW_WORKSPACE should win over WORKSPACE_ROOT: %v", got)
	}
	t.Setenv("OPENCLAW_WORKSPACE", "")
	if got := Workspace(); got != "/tmp/root-ws" {
		t.Fatalf("WORKSPACE_ROOT fallback ignored: %v", got)
	}
	t.Setenv("MARO_WORKSPACE", "/tmp/primary-ws")
	if got := Workspace(); got != "/tmp/primary-ws" {
		t.Fatalf("MARO_WORKSPACE must outrank compat names: %v", got)
	}
}

// The user-config tier moves with MARO_USER_DIR (Python _maro_dir),
// keeping the box's real ~/.maro/config.yml out of tests.
func TestHomeHonorsMaroUserDir(t *testing.T) {
	t.Setenv("MARO_USER_DIR", "/tmp/user-tier")
	if got := Home(); got != "/tmp/user-tier" {
		t.Fatalf("MARO_USER_DIR ignored: %v", got)
	}
}

func TestLoadReportsUnparseableFileInsteadOfSwallowing(t *testing.T) {
	home := t.TempDir()
	ws := filepath.Join(home, "workspace")
	if err := os.MkdirAll(ws, 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("MARO_USER_DIR", home) // isolate the user tier from the box's real ~/.maro
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
