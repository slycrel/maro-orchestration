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
	osuser "os/user"
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
		return expandUser(v)
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
			return expandUser(v)
		}
	}
	h, err := os.UserHomeDir()
	if err != nil {
		return filepath.Join(".maro", "workspace")
	}
	return filepath.Join(h, ".maro", "workspace")
}

// expandUser is the `.expanduser()` both Python resolvers apply to an
// env-var path, and its absence was a whole-store fork (mission-r3
// MEDIUM). A shell does not expand `~` inside a systemd `Environment=`
// line, a Docker `-e`, or a quoted assignment — and this repo's own
// scripts/mint_grounding_census.py writes exactly that shape. Measured
// on MARO_WORKSPACE=~/.maro/workspace:
//
//	CPython -> /home/clawd/.maro/workspace
//	Go      -> the literal "~/.maro/workspace", i.e. a directory named
//	           "~" under the process cwd
//
// The two runtimes then read and write entirely different mission.json,
// memory/ and playbook.md. That is the failure feedback_live_store_probes
// records, in a second spelling.
//
// Python also calls .resolve() on the workspace (not on _maro_dir), which
// absolutizes and follows symlinks. Deliberately NOT ported: it only
// changes an answer when a caller chdirs between two resolutions, and
// resolving symlinks here would make Go disagree with Python about which
// path STRING a probe should assert — the thing that rule is for. Named
// so the next reader knows it was a decision.
// The `~user` form is expanded too. The first cut handled only a leading
// `~` / `~/` under a comment asserting that "~user is a different lookup
// and this box has no such user, so Python leaves it alone too" — false
// twice over, measured (adversarial mission-r4 MEDIUM):
//
//	MARO_WORKSPACE=~clawd/.maro/workspace
//	  py -> /home/clawd/.maro/workspace
//	  go -> "~clawd/.maro/workspace"  (a dir named "~clawd" under cwd)
//
// A whole-store fork, seven lines from the r3 fix it is the second
// spelling of, and the corpus had no `~user` case that could have said so.
//
// RESIDUAL, named: on an UNKNOWN user Python RAISES
// (`RuntimeError: Could not determine home directory.`) and this returns
// the string unexpanded. Workspace/Home have no error channel, and
// inventing one here would be a larger change than the finding; the
// unexpanded path at least cannot be mistaken for a real home. Pinned as
// a divergence rather than left silent.
func expandUser(p string) string {
	if !strings.HasPrefix(p, "~") {
		return p
	}
	if p == "~" || strings.HasPrefix(p, "~/") {
		if h, err := os.UserHomeDir(); err == nil {
			return filepath.Join(h, strings.TrimPrefix(p, "~"))
		}
		return p
	}
	name, rest, _ := strings.Cut(p[1:], "/")
	u, err := osuser.Lookup(name)
	if err != nil || u.HomeDir == "" {
		return p
	}
	return filepath.Join(u.HomeDir, rest)
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
// GetRaw is config.get with no type filtering at all: the stored value when
// the key is PRESENT — a YAML null included — and the default only when it
// is absent.
//
// It exists because Get[any] cannot express that. A Go type assertion to
// `any` FAILS for a nil interface, so `Get[any](cfg, k, 30)` answers 30 for
// a key written `k: ~`, where Python's config.get returns None and the
// caller's own int()/float() then raises. Three callers wanted the raw
// value and all three were reaching for Get[any]; one of them decides
// whether an operator's notify hook runs at all.
func GetRaw(cfg map[string]any, path string, def any) any {
	if v, present := Lookup(cfg, path); present {
		return v
	}
	return def
}

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
// PyYAML, which is YAML **1.1**, and Go reads it with gopkg.in/yaml.v3,
// which is YAML **1.2**. The two libraries disagree about a scalar's TYPE
// before get_bool is ever called, and the disagreement is wider than the
// bool words (mission-r3 MEDIUM corrected an earlier version of this note
// that said it was confined to them). Measured on this box:
//
//	config.yml     PyYAML 1.1        yaml.v3 1.2      get_bool(_, False)
//	flag: on       bool True         string "on"      true  / true    agree
//	flag: off      bool False        string "off"     false / false   agree
//	flag: 08       str "08"          float64 8        false / TRUE    FORK
//	flag: 09       str "09"          float64 9        false / TRUE    FORK
//	flag: 0o10     str "0o10"        int 8            false / TRUE    FORK
//	flag: 1:30     int 90 (!)        string "1:30"    true  / FALSE   FORK
//	flag: 1e2      str "1e2"         float64 100      false / TRUE    FORK
//	flag: 1e+2     str "1e+2"        float64 100      false / TRUE    FORK
//	flag: 1e-2     str "1e-2"        float64 0.01     false / TRUE    FORK
//	flag: 1.0e2    str "1.0e2"       float64 100      false / TRUE    FORK
//	flag: 010      int 8             int 8            true  / true    agree
//	flag: 2026-01-02 datetime.date   time.Time        both warn — agree
//	flag: 2026-1-2 str "2026-1-2"    string           both warn — agree
//
// The exponent family is the part this note MISSED for three rounds, and
// it is the same "Go is the silent side" shape as 08/09: PyYAML's 1.1
// float resolver wants both a decimal point and a SIGNED exponent, so
// every unsigned or dotless spelling stays a string there and resolves to
// a number in yaml.v3.
//
// The date row was also simply WRONG — it claimed `2026-1-2` resolves to
// a datetime.date. It does not: PyYAML's timestamp resolver needs
// zero-padded fields, so `2026-01-02` is a date and `2026-1-2` is the
// string. Both spellings happen to agree across the two runtimes, so the
// row was never a fork at all (adversarial mission-r4 LOW). Every row
// above is now produced by running both libraries, not by reading their
// resolvers — see the rewritten
// TestYAML11And12DisagreeOnMoreThanTheBoolWords, which used to assert
// only Go's side against a hardcoded table and could not have caught this.
//
// The four bool words agree, and that is a COINCIDENCE of those words
// being in truthyStrings/falsyStrings under either reading — not a
// property of the seam. Where the libraries disagree about the value
// rather than the route, the two runtimes take opposite branches of a
// behaviour gate, and on 08/09/0o10 Go is the side that stays SILENT: the
// operator gets no signal from the runtime doing the wrong thing.
//
// Not normalized, because doing it properly means re-resolving every
// scalar under 1.1 rules inside Get/Lookup — its own slice, touching
// every config read in the port. Pinned case by case in
// config_diff_test.go: the agreeing spellings in boolCorpus, the forking
// ones in TestYAML11And12DisagreeOnMoreThanTheBoolWords, which fails if
// either side moves. pyval.Repr renders a time.Time as
// "<unrepresentable>", so the date row's warning text forks too.
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
