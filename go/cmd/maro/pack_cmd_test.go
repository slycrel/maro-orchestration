package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The literal production path: flag parsing → resolveWorkspace →
// pack.Export/Seal/Import/Adopt (adversarial round 2026-08-22: every
// lifecycle test bypassed the CLI, so a flag-wiring regression would not
// have been caught by `go test ./...`).
func TestRunPackLifecycleThroughCLI(t *testing.T) {
	src := t.TempDir()
	for _, d := range []string{filepath.Join(src, "memory"), filepath.Join(src, "skills")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(src, "memory", "standing_rules.jsonl"),
		[]byte(`{"rule_id":"r1","rule":"stage explicit paths only","domain":"git"}`+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(src, "skills", "probe.md"),
		[]byte("# Probe\ncli round trip\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	out := t.TempDir()
	target := t.TempDir()
	// Hermetic ambient config for the import's killswitch read.
	t.Setenv("MARO_USER_DIR", t.TempDir())
	t.Setenv("MARO_WORKSPACE", target)

	if err := runPack([]string{"export", "-name", "clitest", "-label", "cli",
		"-workspace", src, "-out", out}); err != nil {
		t.Fatalf("export: %v", err)
	}
	packPath := filepath.Join(out, "clitest.maropack.tar.gz")
	if _, err := os.Stat(packPath); err != nil {
		t.Fatalf("export wrote no archive: %v", err)
	}

	if err := runPack([]string{"seal", "-pack", packPath}); err == nil {
		t.Fatal("seal without -yes accepted")
	}
	if err := runPack([]string{"seal", "-pack", packPath, "-yes"}); err != nil {
		t.Fatalf("seal: %v", err)
	}

	// -target omitted: resolves through MARO_WORKSPACE (the env contract).
	if err := runPack([]string{"import", "-pack", packPath, "-label", "cli"}); err != nil {
		t.Fatalf("import: %v", err)
	}
	hyps, err := os.ReadFile(filepath.Join(target, "memory", "hypotheses.jsonl"))
	if err != nil || !strings.Contains(string(hyps), "imported-clitest-r1") {
		t.Fatalf("import did not land in the env-resolved workspace: %v %s", err, hyps)
	}

	if err := runPack([]string{"adopt", "-label", "cli", "-target", target, "probe"}); err != nil {
		t.Fatalf("adopt: %v", err)
	}
	adopted, err := os.ReadFile(filepath.Join(target, "skills", "probe.md"))
	if err != nil || !strings.Contains(string(adopted), "imported_from: cli") {
		t.Fatalf("adopt did not stamp provenance: %v %s", err, adopted)
	}
	audit, err := os.ReadFile(filepath.Join(target, "memory", "imports.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	// json.dumps' separators — pack.py writes these rows, and this is a
	// shared audit trail (mission-r8). The `action` key is spread in LAST
	// on the Python side, which is what embedding reproduces here, so the
	// suffix check is also an order check.
	// The leading `, ` is part of the needle: a check that starts at the
	// key survives a mutant that compacts only the item separator
	// (mission-r8 battery).
	if !strings.Contains(string(audit), `, "action": "pack_import"}`) ||
		!strings.Contains(string(audit), `, "action": "adopt"}`) {
		t.Fatalf("audit trail incomplete: %s", audit)
	}

	if err := runPack([]string{"bogus"}); err == nil {
		t.Fatal("unknown subcommand accepted")
	}
	if err := runPack([]string{"export", "-label", "x"}); err == nil {
		t.Fatal("export without -name accepted")
	}
}
