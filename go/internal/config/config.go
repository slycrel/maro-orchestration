// Package config ports maro's two-tier YAML configuration.
//
// Same contract as the Python src/config.py: user-level ~/.maro/config.yml
// provides defaults, workspace-level <workspace>/config.yml overrides it,
// nested maps merge one level deep, and environment variables win over
// both for the paths (MARO_HOME, MARO_WORKSPACE — the same names the
// Python runtime honors, so both runtimes resolve the same stores).
package config

import (
	"os"
	"path/filepath"
	"strings"

	yaml "gopkg.in/yaml.v3"
)

// Home resolves the maro home directory (default ~/.maro).
func Home() string {
	if v := os.Getenv("MARO_HOME"); v != "" {
		return v
	}
	h, err := os.UserHomeDir()
	if err != nil {
		return ".maro"
	}
	return filepath.Join(h, ".maro")
}

// Workspace resolves the runtime workspace (default <home>/workspace).
// MARO_WORKSPACE is the override the Python runtime honors — NOT
// MARO_HOME/MARO_USER_DIR (feedback_live_store_probes, 2026-08-16: a
// session assumed the wrong env var and overwrote a live ledger; the
// resolved path must be asserted before any write, which cmd/maro does
// by printing it first).
func Workspace() string {
	if v := os.Getenv("MARO_WORKSPACE"); v != "" {
		return v
	}
	return filepath.Join(Home(), "workspace")
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
// default. YAML integers arrive as int and floats as float64; Get matches
// the requested type and falls back to def on any mismatch.
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
	// int-vs-float64 tolerance for numeric lookups.
	if want, isFloat := any(def).(float64); isFloat {
		_ = want
		if iv, ok := cur.(int); ok {
			if out, ok2 := any(float64(iv)).(T); ok2 {
				return out
			}
		}
	}
	return def
}
