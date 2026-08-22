// Package config ports maro's two-tier YAML configuration.
//
// Same contract as the Python src/config.py: user-level ~/.maro/config.yml
// provides defaults, workspace-level <workspace>/config.yml overrides it,
// nested maps merge one level deep, and environment variables win over
// both for the paths — the SAME names, in the SAME order, that the Python
// runtime honors, so both runtimes resolve the same stores.
package config

import (
	"os"
	"path/filepath"
	"strings"

	yaml "gopkg.in/yaml.v3"
)

// Home resolves the user-level maro directory (default ~/.maro), which
// holds the user config tier ONLY. It mirrors Python config._maro_dir:
// MARO_USER_DIR overrides (tests point it at tmp), and — critically — it
// has NO influence on Workspace below. The 2026-08-16 live-ledger
// incident happened because an operator set MARO_HOME expecting it to
// move the store; Python reads no such variable, and after this port's
// adversarial round (Architect, 2026-08-22) neither does Go.
func Home() string {
	if v := os.Getenv("MARO_USER_DIR"); v != "" {
		return v
	}
	h, err := os.UserHomeDir()
	if err != nil {
		return ".maro"
	}
	return filepath.Join(h, ".maro")
}

// Workspace resolves the runtime workspace, matching Python
// config.workspace_root exactly: MARO_WORKSPACE, then the legacy compat
// names OPENCLAW_WORKSPACE and WORKSPACE_ROOT, then ~/.maro/workspace
// unconditionally — never derived from Home()
// (feedback_live_store_probes, 2026-08-16: a session assumed the wrong
// env var and overwrote a live ledger; the resolved path must be
// asserted before any write, which cmd/maro does by printing it first).
func Workspace() string {
	for _, name := range []string{"MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"} {
		if v := os.Getenv(name); v != "" {
			return v
		}
	}
	h, err := os.UserHomeDir()
	if err != nil {
		return filepath.Join(".maro", "workspace")
	}
	return filepath.Join(h, ".maro", "workspace")
}

// Load reads and merges the two config tiers. A missing or unparseable
// file contributes nothing rather than failing the boot — matching the
// Python runtime's tolerance — but an unparseable file is REPORTED via
// the returned warnings, never swallowed (no-silent-errors doctrine).
func Load() (cfg map[string]any, warnings []string) {
	user := readYAML(filepath.Join(Home(), "config.yml"), &warnings)
	ws := readYAML(filepath.Join(Workspace(), "config.yml"), &warnings)
	return Merge(user, ws), warnings
}

func readYAML(path string, warnings *[]string) map[string]any {
	raw, err := os.ReadFile(path)
	if err != nil {
		if !os.IsNotExist(err) {
			*warnings = append(*warnings, path+": "+err.Error())
		}
		return nil
	}
	var m map[string]any
	if err := yaml.Unmarshal(raw, &m); err != nil {
		*warnings = append(*warnings, path+": "+err.Error())
		return nil
	}
	return m
}

// Merge overlays over onto base; nested maps merge one level deep (the
// documented Python contract — deeper nests replace wholesale).
func Merge(base, over map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range base {
		out[k] = v
	}
	for k, v := range over {
		bm, baseIsMap := out[k].(map[string]any)
		om, overIsMap := v.(map[string]any)
		if baseIsMap && overIsMap {
			m := map[string]any{}
			for k2, v2 := range bm {
				m[k2] = v2
			}
			for k2, v2 := range om {
				m[k2] = v2
			}
			out[k] = m
			continue
		}
		out[k] = v
	}
	return out
}

// Get resolves a dotted path ("inspector.breach_threshold") with a typed
// default. YAML integers arrive as int and floats as float64; Get
// tolerates the mismatch in BOTH directions (an operator writing
// `max_steps: 8.0` still gets their 8 — adversarial round 2026-08-22
// caught the one-directional version silently discarding it).
//
// Honest residual: any OTHER type mismatch (a quoted "4000", a
// string-typed "false") silently falls back to def, same as Python's
// config.get. Making that loud needs a warnings channel like Load's —
// deferred until a caller needs it, noted in PORT.md.
func Get[T any](cfg map[string]any, path string, def T) T {
	var cur any = cfg
	for _, part := range strings.Split(path, ".") {
		m, ok := cur.(map[string]any)
		if !ok {
			return def
		}
		cur, ok = m[part]
		if !ok {
			return def
		}
	}
	if v, ok := cur.(T); ok {
		return v
	}
	switch any(def).(type) {
	case float64: // stored as YAML int, requested as float64
		if iv, ok := cur.(int); ok {
			if out, ok2 := any(float64(iv)).(T); ok2 {
				return out
			}
		}
	case int: // stored as YAML float, requested as int (integral only)
		if fv, ok := cur.(float64); ok && fv == float64(int(fv)) {
			if out, ok2 := any(int(fv)).(T); ok2 {
				return out
			}
		}
	}
	return def
}
