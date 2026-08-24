// Package config ports maro's two-tier YAML configuration.
//
// Same contract as the Python src/config.py: user-level ~/.maro/config.yml
// provides defaults, workspace-level <workspace>/config.yml overrides it,
// nested maps merge one level deep, and environment variables win over
// both for the paths — the SAME names, in the SAME order, that the Python
// runtime honors, so both runtimes resolve the same stores.
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	yaml "gopkg.in/yaml.v3"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
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
	return LoadFor(Workspace())
}

// LoadFor is Load against a NAMED workspace instead of the ambient one.
//
// Python has no equivalent because its playbook, memory and config paths
// are all module-level and all resolve from the same env: a Python verb
// cannot be pointed at one workspace and read another's config. This
// port's verbs DO take a workspace argument, which quietly makes that
// mismatch possible — a verb handed `ws` but calling Load() reads
// MARO_WORKSPACE's config while writing ws's files (adversarial r9
// MEDIUM: the failure direction is destructive, since a retention TTL
// from the wrong file expires alarms out of a document whose own config
// said to keep them).
//
// Any verb that takes a workspace argument must use this, not Load.
func LoadFor(workspaceDir string) (cfg map[string]any, warnings []string) {
	user := readYAML(filepath.Join(Home(), "config.yml"), &warnings)
	ws := readYAML(filepath.Join(workspaceDir, "config.yml"), &warnings)
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
	cur, present := Lookup(cfg, path)
	if !present {
		return def
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

// Python's str-normalizing boolean sets, verbatim from config.py:
//
//	_TRUTHY_STRINGS = frozenset({"true", "1", "yes", "on"})
//	_FALSY_STRINGS  = frozenset({"false", "0", "no", "off", ""})
//
// Note "" is FALSY, not unrecognized — an empty quoted value is a
// deliberate off, not a typo, and it must not fall through to the
// default.
var (
	truthyStrings = map[string]bool{"true": true, "1": true, "yes": true, "on": true}
	falsyStrings  = map[string]bool{"false": true, "0": true, "no": true, "off": true, "": true}
)

// GetBool ports config.get_bool: a boolean read that normalizes the
// STRING forms, because a quoted YAML value arrives as a string and
// Python's bool("false") is True. For a flag that gates behaviour — a
// revert lever above all — that error direction silently defeats the
// operator, so an unrecognized value falls back to the default with a
// warning rather than to truthiness.
//
// The warning is RETURNED rather than logged, matching this package's own
// idiom (Load returns its warnings too) and the no-silent-errors
// doctrine. It is "" when Python would not have warned. Python's line is
//
//	logging.getLogger("maro.config").warning(
//	    "config.get_bool: unrecognized value for %s: %r — using default %s",
//	    key, val, default)
//
// so the text is reproduced exactly, %r included: a divergence in that
// string is a divergence in a shared log.
//
// PARITY NOTE, and it is the sharp one here. Python reads this file with
// PyYAML, which is YAML **1.1**, where bare `on`/`off`/`yes`/`no` are
// BOOLEANS. Go reads it with gopkg.in/yaml.v3, which is YAML **1.2**,
// where those same words are plain strings. The two libraries disagree
// about the type before get_bool is ever called. It happens that both
// runtimes reach the same answer anyway — 1.1 hands Python a real bool,
// 1.2 hands Go a string that is in truthyStrings/falsyStrings — but they
// reach it by different routes, and the agreement is a coincidence of
// these particular four words being in both sets. Pinned case by case in
// config_diff_test.go so that if either set ever changes, the test says
// so instead of the store quietly forking.
func GetBool(cfg map[string]any, path string, def bool) (value bool, warning string) {
	val, present := Lookup(cfg, path)
	if !present {
		// Python: get(key, default) returns the default, which IS a
		// bool, so the isinstance(val, bool) arm returns it. No warning.
		return def, ""
	}
	switch v := val.(type) {
	case bool:
		return v, ""
	case int:
		// Python: bool(val) for int and float alike.
		return v != 0, ""
	case int64:
		return v != 0, ""
	case uint64:
		// yaml.v3 promotes an integer past MaxInt64 to uint64; Python's
		// ints are unbounded and bool() of any nonzero one is True.
		return v != 0, ""
	case float64:
		// NaN != 0 is true in Go and bool(nan) is True in Python; -0.0
		// is false in both. The naive comparison is the exact port.
		return v != 0, ""
	case string:
		// EQUIVALENT-MUTANT NOTE on the Lower half only (the Strip half
		// is separable and pinned: str.strip() removes U+001C..U+001F and
		// strings.TrimSpace does not, which flips "\x1c" from FALSY to
		// unrecognized). Substituting strings.ToLower here survives the
		// battery, and provably so: both sets are pure ASCII, and across
		// all 1,114,112 code points exactly ONE — U+0130 — has a
		// str.lower() longer than a single character, mapping to "i" plus
		// a combining dot, which is in neither set. The other divergence
		// between full and simple case mapping, final sigma, is
		// Greek-to-Greek. No case-mapping difference can land on a
		// different ASCII answer, so the outcome cannot fork.
		//
		// pytext.Lower stays anyway: the equivalence is a property of
		// these two sets being ASCII, not of the function.
		s := pytext.Lower(pytext.Strip(v))
		if truthyStrings[s] {
			return true, ""
		}
		if falsyStrings[s] {
			return false, ""
		}
	}
	return def, fmt.Sprintf(
		"config.get_bool: unrecognized value for %s: %s — using default %s",
		path, pyval.Repr(val), pyval.Repr(def))
}

// Lookup is Get's presence half: it reports whether the path EXISTS,
// separately from what it holds. Get cannot express the difference —
// an absent key and an explicit `key: null` both make it return the
// default — and for the keys Python feeds to a coercing constructor that
// difference is behaviour, not pedantry. Python's captains_log does
//
//	rotate_mb = float(_cfg_get("captains_log.rotate_mb", 5.0))
//
// so an absent key yields 5.0 while an explicit null yields float(None),
// which RAISES and sends rotate_mb AND rotate_keep back to their
// defaults jointly. Measured before this seam existed: on
// `{rotate_mb: null, rotate_keep: 50}` Python rotated at (5.0, 1000) and
// Go at (5.0, 50), disagreeing about how much of a SHARED captain's log
// stays live (adversarial r7 LOW).
//
// A nil second-return-free variant is not enough: the caller needs
// present-and-nil to be distinguishable from absent, which is exactly
// the boolean.
func Lookup(cfg map[string]any, path string) (any, bool) {
	var cur any = cfg
	for _, part := range strings.Split(path, ".") {
		m, ok := cur.(map[string]any)
		if !ok {
			return nil, false
		}
		cur, ok = m[part]
		if !ok {
			return nil, false
		}
	}
	return cur, true
}
