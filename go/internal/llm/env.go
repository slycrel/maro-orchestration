package llm

import (
	"os"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/config"
)

// Uniform CLAUDE_CODE_MAX_OUTPUT_TOKENS ceiling for utility (no-tools)
// calls: a runaway circuit-breaker, NOT contract enforcement (Python
// _NO_TOOLS_OUTPUT_CEILING, Jeremy decree 2026-07-29: caps exist for
// containment only). The CLI cap counts THINKING tokens, so an
// answer-sized cap decapitates reasoning — this is sized ~4x the largest
// observed legitimate reasoning burn and under the CLI's 32000 default.
// Agentic calls are deliberately uncapped here: multi-turn output
// legitimately exceeds any utility-sized ceiling.
const noToolsOutputCeiling = 16000

// bashOutputCapDefault mirrors Python _BASH_OUTPUT_CAP_DEFAULT.
const bashOutputCapDefault = 24000

// envOverride is one child-env decision: Set=false means "unset this key
// in the child", which is NOT the same as expressing no opinion — Python
// learned that distinction the hard way (adversarial review 2026-07-27:
// the documented higher-precedence disable silently did nothing beside
// an exported operator value).
type envOverride struct {
	Key   string
	Value string
	Set   bool
}

// bashOutputCapEnv ports Python _bash_output_cap_env: the per-tool-call
// Bash output cap for an agentic subprocess (oversized output is
// persisted to a file by the CLI and only a capped slice reaches the
// model's context — the structural half of the tire-runs fix).
//
// Precedence: MARO_BASH_MAX_OUTPUT_CHARS (0 disables, and disable means
// UNSET in the child) > an operator's own BASH_MAX_OUTPUT_LENGTH already
// in the environment (inherited as-is) > config
// llm.subprocess.bash_max_output_chars > bashOutputCapDefault.
// A nil return means "no opinion, inherit everything".
func bashOutputCapEnv() []envOverride {
	if raw, ok := os.LookupEnv("MARO_BASH_MAX_OUTPUT_CHARS"); ok {
		if cap, err := strconv.Atoi(raw); err == nil {
			if cap > 0 {
				return []envOverride{{Key: "BASH_MAX_OUTPUT_LENGTH",
					Value: strconv.Itoa(cap), Set: true}}
			}
			// Explicit disable outranks an inherited operator value.
			return []envOverride{{Key: "BASH_MAX_OUTPUT_LENGTH", Set: false}}
		}
		// Not an int: fall through, matching Python's warn-and-ignore.
	}
	if _, ok := os.LookupEnv("BASH_MAX_OUTPUT_LENGTH"); ok {
		return nil // operator already governs the CLI directly; inherit as-is
	}
	cfg, _ := config.Load()
	cap := config.Get(cfg, "llm.subprocess.bash_max_output_chars", bashOutputCapDefault)
	if cap > 0 {
		return []envOverride{{Key: "BASH_MAX_OUTPUT_LENGTH",
			Value: strconv.Itoa(cap), Set: true}}
	}
	return nil
}

// childEnv materializes the inherited environment plus overrides. Unset
// overrides remove the key; set overrides replace (or append) it.
func childEnv(overrides []envOverride) []string {
	if len(overrides) == 0 {
		return nil // exec's nil = inherit, no copy
	}
	drop := map[string]bool{}
	for _, o := range overrides {
		drop[o.Key] = true
	}
	var env []string
	for _, kv := range os.Environ() {
		key := kv
		if i := strings.IndexByte(kv, '='); i >= 0 {
			key = kv[:i]
		}
		if !drop[key] {
			env = append(env, kv)
		}
	}
	for _, o := range overrides {
		if o.Set {
			env = append(env, o.Key+"="+o.Value)
		}
	}
	return env
}
