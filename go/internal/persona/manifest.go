package persona

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// ToDict is persona_to_dict: `asdict(spec)` plus a preview key.
//
// The key order is the DATACLASS FIELD ORDER with system_prompt_preview
// appended, because that is where `d["system_prompt_preview"] = ...` puts
// it, and a dict rendered to JSON writes insertion order. A Go map cannot
// hold it, so this returns a pyval.Obj.
//
// The preview is `spec.system_prompt[:200].replace("\n", " ")` — sliced by
// CODE POINT, and NOT stripped. Its manifest sibling four lines away IS
// stripped, and the two are otherwise identical; a shared helper would have
// to take a flag, so they stay separate the way the Python has them.
func ToDict(spec *Spec) pyval.Obj {
	return pyval.Obj{
		{Key: "name", Val: spec.Name},
		{Key: "role", Val: spec.Role},
		{Key: "model_tier", Val: spec.ModelTier},
		{Key: "tool_access", Val: pyval.List(copyList(spec.ToolAccess))},
		{Key: "memory_scope", Val: spec.MemoryScope},
		{Key: "communication_style", Val: spec.CommunicationStyle},
		{Key: "system_prompt", Val: spec.SystemPrompt},
		{Key: "hooks", Val: pyval.List(copyList(spec.Hooks))},
		{Key: "composes", Val: pyval.List(copyList(spec.Composes))},
		{Key: "source_file", Val: spec.SourceFile},
		{Key: "system_prompt_preview", Val: strings.ReplaceAll(clipRunes(spec.SystemPrompt, 200), "\n", " ")},
	}
}

// routingKeywords is _PERSONA_ROUTING_KEYWORDS. It is NOT the routing table
// — the module comment claims it "mirrors persona_for_goal routing table"
// and it does not: the lists are shorter, differently worded, and two
// personas that route (`strategist` via "competitive", `ops` via
// "infrastructure") advertise trigger words the router does not actually
// match on. Ported verbatim, drift included, because it is what lands in a
// manifest.json both runtimes write.
var routingKeywords = map[string][]string{
	"health-researcher":              {"health", "medical", "nutrition", "disease", "clinical", "symptom"},
	"legal-researcher":               {"legal", "law", "contract", "regulation", "compliance", "statute"},
	"strategist":                     {"strategy", "roadmap", "competitive", "market", "planning", "vision"},
	"creative-director":              {"creative", "brand", "marketing", "campaign", "design", "story"},
	"scrapling-adaptive-web-recon":   {"scrape", "scraping", "crawl", "web extraction", "site data"},
	"systems-design-architect-coach": {"architecture", "distributed", "system design", "microservice", "infra"},
	"critic":                         {"critique", "review", "failure mode", "weakness", "evaluate", "assess"},
	"simplifier":                     {"simplify", "too complex", "remove", "dead code", "reduce"},
	"research-assistant-deep-synth":  {"research", "analyze", "summarize", "literature", "investigate"},
	"builder":                        {"build", "implement", "create", "code", "develop", "write"},
	"ops":                            {"deploy", "monitor", "ops", "infrastructure", "pipeline", "automate"},
	"finance-analyst":                {"finance", "invest", "portfolio", "market", "trading", "profit"},
	"psyche-researcher":              {"psychology", "neurology", "cognitive", "mental", "behavior"},
	"reporter":                       {"consolidate", "synthesize", "combine outputs", "final report", "merge results"},
}

// GenerateManifest builds the machine-readable agent capability list.
//
// Two things it does NOT do, both easy to "fix" into a divergence:
//
//   - The `name` field is the name REGISTRY.LIST RETURNED, not spec.Name.
//     They differ whenever a file's frontmatter name and its stem disagree
//     — a `zeta.md` declaring `name: alpha` is listed as "alpha", loaded
//     through the stem-or-name match, and its entry says "alpha" while its
//     role and description come from the file. Measured.
//   - The final `manifest.sort(key=name)` is kept even though List already
//     returns sorted names, because the two sorts are not the same sort:
//     List sorts the names it CHOSE, and duplicates (two files, same
//     frontmatter name) survive into this list and are re-ordered stably.
func GenerateManifest(reg *Registry) []pyval.Obj {
	manifest := []pyval.Obj{}
	for _, name := range reg.List() {
		spec := reg.Load(name)
		if spec == nil {
			continue
		}
		triggers := routingKeywords[name] // a missing key is Python's `[]`
		trigList := make(pyval.List, len(triggers))
		for i, t := range triggers {
			trigList[i] = t
		}
		manifest = append(manifest, pyval.Obj{
			{Key: "name", Val: name},
			{Key: "role", Val: spec.Role},
			{Key: "model_tier", Val: spec.ModelTier},
			{Key: "tool_access", Val: pyval.List(copyList(spec.ToolAccess))},
			{Key: "memory_scope", Val: spec.MemoryScope},
			{Key: "trigger_keywords", Val: trigList},
			{Key: "composes", Val: pyval.List(copyList(spec.Composes))},
			// `[:200].strip().replace("\n", " ")` — slice, THEN strip,
			// THEN replace. Stripping first would keep 200 characters of
			// content where Python keeps fewer.
			{Key: "description", Val: strings.ReplaceAll(
				pytext.Strip(clipRunes(spec.SystemPrompt, 200)), "\n", " ")},
		})
	}
	sort.SliceStable(manifest, func(i, j int) bool {
		return manifest[i].GetString("name") < manifest[j].GetString("name")
	})
	return manifest
}

// ErrYAMLManifestNotPorted is what SaveManifest answers for fmt="yaml".
//
// PyYAML's `yaml.dump(..., default_flow_style=False, allow_unicode=True)`
// is not reproducible by gopkg.in/yaml.v3, and the differences are every
// line of the file rather than a corner: PyYAML SORTS keys (sort_keys
// defaults True, so the field order this package is careful about is
// discarded), it FOLDS a long scalar onto continuation lines, and it quotes
// with single quotes and doubles an embedded one. Measured — the fixture is in
// manifest_diff_test.go.
//
// The fold is a WHITESPACE break near a width default, not a hard column
// ceiling; two guesses about it were measured wrong before this comment was
// written. A 120-character run of "x" does not fold at all (nowhere to
// break), and a scalar that DOES fold still emits a first line of 83 columns
// for the test's fixture, because the break point is chosen before the
// closing quote is appended.
//
// Falling back to JSON, which is what Python does when PyYAML is missing,
// was rejected: on this box PyYAML is installed, so the port would take a
// branch CPython does not and write manifest.json where CPython writes
// manifest.yaml. An error names the gap instead of hiding it in a suffix.
var ErrYAMLManifestNotPorted = errors.New(
	"persona: the yaml manifest format is not ported — PyYAML's dump " +
		"(sorted keys, whitespace folding, single-quote style) is not " +
		"reproducible with gopkg.in/yaml.v3; use fmt=\"json\"")

// SaveManifest writes the agent capability manifest to disk and returns the
// path written.
//
// outputPath "" is Python's `output_path is None`, which resolves to
// `output_root()/agents/manifest.<fmt>`. Python's resolution reads
// orch_items.output_root() and falls back to `./manifest.<fmt>` when the
// import fails; this port takes the output root as an argument for the same
// reason Registry takes its directories (one resolution order, passed in).
//
// The content is `json.dumps({"agents": manifest}, indent=2,
// ensure_ascii=False)` plus a trailing newline, written through the atomic
// temp+rename both runtimes use.
func SaveManifest(outputRoot, outputPath, format string, reg *Registry) (string, error) {
	if format == "yaml" {
		return "", ErrYAMLManifestNotPorted
	}
	if outputPath == "" {
		outputPath = filepath.Join(outputRoot, "agents", "manifest."+format)
	}
	// Python mkdirs the parent HERE, before generating, so the directory
	// exists even on a run that then fails. AtomicWrite would mkdir too;
	// this keeps the statement order and therefore that side effect.
	if err := os.MkdirAll(filepath.Dir(outputPath), record.NewDirMode); err != nil {
		return "", err
	}
	manifest := GenerateManifest(reg)
	payload := pyval.Obj{{Key: "agents", Val: objList(manifest)}}
	content, err := pyval.DumpsIndent2Raw(payload)
	if err != nil {
		return "", err
	}
	if err := record.AtomicWrite(outputPath, []byte(content+"\n")); err != nil {
		return "", fmt.Errorf("persona: writing %s: %w", outputPath, err)
	}
	return outputPath, nil
}

func objList(rows []pyval.Obj) pyval.List {
	out := make(pyval.List, len(rows))
	for i, r := range rows {
		out[i] = r
	}
	return out
}
