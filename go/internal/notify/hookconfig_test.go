package notify

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// TestTheHookReadsTheWorkspaceItWasHanded pins which config file decides
// whether an operator's substrate hears anything.
//
// Python cannot get this wrong: config.get resolves through MARO_WORKSPACE,
// which is the same workspace emit is writing. This port's verbs take `ws`
// as an argument, which quietly makes reading one workspace's hook config
// while writing another's ledgers possible — the adversarial-r9 MEDIUM,
// recommitted in a function written after it.
//
// Nothing could have caught it: every existing hook test passes Cfg
// explicitly, so the fallback branch had no coverage at all. **A default
// nobody exercises is a default nobody has read.**
func TestTheHookReadsTheWorkspaceItWasHanded(t *testing.T) {
	ws := t.TempDir()
	ambient := t.TempDir()
	// The ambient workspace registers NO command; the one being written
	// registers one. Under config.Load() the hook is silent.
	if err := os.WriteFile(filepath.Join(ws, "config.yml"),
		[]byte("notify:\n  command: bash notify.sh\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("MARO_WORKSPACE", ambient)

	rec := &recorder{}
	if !Emit(context.Background(), ws, "escalation",
		map[string]any{"handle_id": "h-1"},
		Options{Exec: rec.fn, Env: []string{"PATH=/bin"}}) {
		t.Fatal("the hook did not run: emit read the ambient workspace's config, " +
			"not the one it was handed and is writing ledgers into")
	}
	if rec.command != "bash notify.sh" {
		t.Errorf("command = %q", rec.command)
	}
	// And the ledgers really did go to ws, so the two halves genuinely
	// disagree when the config read is wrong.
	if _, err := os.Stat(filepath.Join(ws, "output", "escalations.jsonl")); err != nil {
		t.Errorf("the escalation ledger is not in the workspace that was passed: %v", err)
	}
}

// TestTheHookTimeoutIsFloatedNotAsserted covers the other half of the same
// config read.
//
// `float(_config_get("notify.timeout_seconds", 30))` coerces — a YAML
// `"45"` is 45.0 in Python — and it has NO try around it, so a non-numeric
// value propagates to emit's outer handler: the hook does not run and emit
// returns False, with the two ledger writes above it already done. A
// defaulting read would have run a command Python declined to run, at a
// timeout nobody configured.
func TestTheHookTimeoutIsFloatedNotAsserted(t *testing.T) {
	for _, c := range []struct {
		name    string
		val     any
		wantRun bool
		want    time.Duration
	}{
		{"an int", 45, true, 45 * time.Second},
		{"a float", 4.5, true, 4500 * time.Millisecond},
		{"a quoted number", "45", true, 45 * time.Second},
		{"a quoted float", " 4.5 ", true, 4500 * time.Millisecond},
		{"a bool", true, true, time.Second},
		{"prose", "soon", false, 0},
		{"a null", nil, false, 0},
		{"a list", []any{45}, false, 0},
	} {
		t.Run(c.name, func(t *testing.T) {
			rec := &recorder{}
			cfg := map[string]any{"notify": map[string]any{
				"command":         "bash notify.sh",
				"timeout_seconds": c.val,
			}}
			ran := Emit(context.Background(), t.TempDir(), "escalation",
				map[string]any{"handle_id": "h-1"},
				Options{Cfg: cfg, Exec: rec.fn, Env: []string{"PATH=/bin"}})
			if ran != c.wantRun {
				t.Fatalf("emit reported %v, want %v (calls=%d)", ran, c.wantRun, rec.calls)
			}
			if !c.wantRun {
				if rec.calls != 0 {
					t.Errorf("the hook ran %d time(s); float() raises for this value "+
						"and CPython never reaches subprocess.run", rec.calls)
				}
				return
			}
			if rec.timeout != c.want {
				t.Errorf("timeout = %v, want %v", rec.timeout, c.want)
			}
		})
	}
}
