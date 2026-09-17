// Package defaults is the Go engine's defaults registry: the one place
// that says what a fresh install runs, paired with go/DEFAULTS.md by a
// census test in both directions. The Python engine's rule holds here —
// a capability defaults ON when it only adds internal evidence, and OFF
// when it spends money, reaches the network, or acts outward — and this
// registry is how a new default is forced to say which it is.
//
// The census is deliberately NOT the Python one: docs/DEFAULTS.md is
// scanned by tests/test_defaults_doc.py, whose reverse lane requires a
// reader in src/ for every dotted key in a table row. A Go key in that
// table would fail the Python suite, so the Go table lives in
// go/DEFAULTS.md and docs/DEFAULTS.md points at it in prose.
package defaults

import (
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/judgment"
)

// A Default is one config key: what it is set to out of the box, the
// flag that changes it, and why that value and not another.
type Default struct {
	Key   string // dotted config key
	Value string // what a fresh install runs ("" renders as (empty))
	Flag  string // the CLI flag that changes it, if any
	Why   string
}

// Render is the value as go/DEFAULTS.md prints it.
func (d Default) Render() string {
	if d.Value == "" {
		return "(empty)"
	}
	return d.Value
}

// List is the registry. Adding a default here without adding its row to
// go/DEFAULTS.md fails TestEveryDefaultIsDocumented, and vice versa.
func List() []Default {
	return []Default{
		{
			Key:   "judgment.provider",
			Value: judgment.DefaultProvider,
			Flag:  "--judge-provider",
			Why:   "the incumbent generative judge over the run's own backend: this seam changes how a judgment is asked, not who answers it, so a fresh install behaves exactly as before.",
		},
		{
			Key:   "judgment.shadow",
			Value: "",
			Flag:  "--judge-shadow",
			Why:   "OFF: a shadow arm reaches the network and spends money on every verdict. Evidence-gathering never turns itself on because the code shipped.",
		},
		{
			Key:   "judgment.timeout",
			Value: judgment.DefaultTimeout.String(),
			Flag:  "",
			Why:   "a judgment is a single small request; past a minute it is a hang, not a slow answer. It is a CEILING: a wire or hosted provider clamps any longer caller budget to it, and every shadow is asked under it, so measurement never holds delivery for the executor's twenty minutes.",
		},
		{
			Key:   "judgment.jev.url",
			Value: judgment.JevBaseURL,
			Flag:  "",
			Why:   "TypeSafe's System One endpoint. The key is read from the secrets store by name, never from the environment alone.",
		},
		{
			Key:   "judgment.jev.model",
			Value: judgment.JevModel,
			Flag:  "",
			Why:   "the vendor's moving latest; pinning a version here would rot silently.",
		},
		{
			Key:   "judgment.jev.key_name",
			Value: judgment.JevKeyName,
			Flag:  "",
			Why:   "a NAME in the secrets store, so the value never appears in a config file, a record, or a log line.",
		},
		{
			Key:   "judgment.hosted.url",
			Value: judgment.HostedBaseURL,
			Flag:  "--hosted-url",
			Why:   "the cheap hosted tier, mirroring the Python engine's hosted-free ladder. Groq (https://api.groq.com/openai/v1) is a flag, not a code change.",
		},
		{
			Key:   "judgment.hosted.model",
			Value: judgment.HostedModel,
			Flag:  "--hosted-model",
			Why:   "gemini-flash-lite: the cheapest tier that cleared the 14-case validation corpus on the Python side (2026-07-16).",
		},
		{
			Key:   "judgment.hosted.key_name",
			Value: judgment.HostedKeyName,
			Flag:  "--hosted-key",
			Why:   "a NAME in the secrets store, same rule as the Jev key.",
		},
		{
			Key:   "judgment.pcd.url",
			Value: judgment.DefaultPCDURL,
			Flag:  "--pcd-url",
			Why:   "the local System One sidecar on the M1: no auth, no spend, optional and experimental. Unreachable is a skipped provider, never a failed run.",
		},
	}
}

// Lookup returns the registered default for a key.
func Lookup(key string) (Default, error) {
	for _, d := range List() {
		if d.Key == key {
			return d, nil
		}
	}
	return Default{}, fmt.Errorf("defaults: %q is not registered (add it to internal/defaults and go/DEFAULTS.md)", key)
}
