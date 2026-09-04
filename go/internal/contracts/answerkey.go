package contracts

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// keyEntry is one vocabulary value: meaning, a concrete wire example, and the
// evidence crumb (how the fact was established). The vocabulary ships as an
// answer key beside the files, otherwise it is jargon with a confident face.
type keyEntry struct{ Key, Value, Meaning, Wire, Evidence string }

var answerKey = []keyEntry{
	{"lifecycle", "stable", "shape chosen AND a new consumer could adopt it as-is; generate and guard", `"lifecycle": "stable"`, "contract-testing-input.md §5"},
	{"lifecycle", "transitional", "meant to die; route + failure semantics only; deprecation lifecycle applies", `"lifecycle": "transitional"`, "ibid."},
	{"lifecycle", "internal-loose", "deliberately unformalized internal edge; warnings stand as accepted risk", `"lifecycle": "internal-loose"`, "ibid."},
	{"lifecycle", "hardened-legacy", "wrong-but-shipping; guard like stable PLUS design_flag (what is wrong, who owns it, sunset trigger); wins ties with stable", `"lifecycle": "hardened-legacy", "design_flag": "…"`, "ibid."},
	{"lifecycle", "design-pending", "shape unowned or contested; suppress test generation; escalation file beside the pair", `"lifecycle": "design-pending"`, "ibid."},
	{"provenance", "supplied", "stated by the owner (issue, design doc, the owner in the room)", `"provenance": "supplied"`, "contract-testing-input.md §8"},
	{"provenance", "inferred", "read off the implementation; evidence of what IS, not what was MEANT", `"provenance": "inferred"`, "ibid."},
	{"absence", "omitted", "the key is absent from the wire", `{"run_id" absent}`, "generated: Omittable=true (omitempty/pointer/slice/map)"},
	{"absence", "null", "the key is present with a JSON null", `"supersedes": null`, "measured per field"},
	{"absence", "empty", "the key is present with an empty value", `"attempt": 0`, "measured per field"},
	{"absence", "never", "the writer always emits a value; absence cannot occur", `always present`, "declared; executable as a writer pin"},
	{"on_absence", "tolerated", "a reader proceeds with the field unset", "—", "reference-reader backward test"},
	{"on_absence", "default:<v>", "a reader substitutes v", `"on_absence": "default:0"`, "reference-reader backward test"},
	{"on_absence", "rejected", "a reader refuses the record", "—", "reference-reader backward test"},
	{"unknown_value", "accepted-unchanged", "an unknown vocabulary value is forwarded verbatim", `"envelope": "future" → kept`, "reference-reader forward test"},
	{"unknown_value", "rejected", "an unknown value refuses the record", "—", "reference-reader forward test"},
	{"unknown_value", "replaced-with:<v>", "an unknown value is mapped to v", `"replaced-with:unset"`, "reference-reader forward test"},
	{"used_for", "display-only | routing | authorization | money | storage-key | identity", "what decisions read the field; authorization + accepted-unchanged is ILLEGAL", `"used_for": "identity"`, "report.go illegal combinations"},
	{"constraint", "defined", "a stated, executable constraint (pattern)", `"constraint": "defined", "pattern": "^s256v1:[0-9a-f]{64}$"`, "report.go requires a pattern"},
	{"constraint", "unconstrained", "someone LOOKED and decided any value of the type is legal; executable (absurd value accepted). Thought fields: by decree (D16)", `"constraint": "unconstrained"`, "thought_test.go feeds 8 MiB / empty / non-UTF-8"},
	{"constraint", "undefined", "nobody looked — a WARNING, never silenced", "(absent)", "report.go"},
	{"measured_by", "<how> | not-re-runnable-here", "how a measured claim was established; not-re-runnable-here is the honest answer for a fact CI cannot re-establish, never a licence to omit", `"measured_by": "thought_test.go TestUnconstrainedBodies"`, "contract-testing-input.md §16"},
	{"envelope", "production | control | experimental", "the population; from the registry, never the value's marker", `"envelope": "production"`, "record/registry.go; record_test.go TestRegistryIsTheAuthority"},
	{"schema", "<kind>/<n>", "contract version of the kind (D3); readers accept 1..n per declared absence semantics", `"schema": "outcome/2"`, "record.SchemaVer.Parse"},
	{"retention", "forever | bounded | audit-only", "what happens to rows over time; audit-only must name its read surface", "—", "record.Spec census"},
}

// WriteAnswerKey renders the vocabulary and the record census.
func WriteAnswerKey(dir Dir) error {
	var b strings.Builder
	b.WriteString("# Contracts answer key (GENERATED — edit answerkey.go, not this file)\n\n")
	b.WriteString("Every key a `.declared.json` may use: the meaning, a concrete wire example, and the evidence crumb. Someone saying \"wha? huh?\" follows the crumb and can disagree with evidence rather than a shrug.\n\n")
	b.WriteString("| key | value | meaning | wire example | evidence |\n|---|---|---|---|---|\n")
	for _, e := range answerKey {
		fmt.Fprintf(&b, "| `%s` | `%s` | %s | `%s` | %s |\n", e.Key, e.Value, e.Meaning, e.Wire, e.Evidence)
	}
	b.WriteString("\n## Three states, one severity model\n\n| state | written as | surfaces as |\n|---|---|---|\n| derived | the generated file | asserted automatically |\n| declared | a line in `.declared.json` | asserted; fails if the system disagrees |\n| undefined | absent from both | **warning** in the report |\n\nErrors, exactly three ways: two derived facts contradict; a declared line fails its test; an illegal combination.\n")
	if err := os.MkdirAll(string(dir), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(string(dir), "README.md"), []byte(b.String()), 0o644); err != nil {
		return err
	}
	return WriteCensus(dir)
}

// WriteCensus renders the record census from the registry: writer,
// authoritative reader, decision, retention — a kind with no consumer is not
// written (design note §14).
func WriteCensus(dir Dir) error {
	var b strings.Builder
	b.WriteString("# Record census (GENERATED from record.Register calls)\n\n")
	b.WriteString("| kind | envelope | schema | writer | authoritative reader | decision it affects | retention |\n|---|---|---|---|---|---|---|\n")
	for _, s := range record.All() {
		fmt.Fprintf(&b, "| `%s` | %s | `%s/%d` | %s | %s | %s | %s |\n", s.Kind, s.Envelope, s.Kind, s.Version, s.Writer, s.Reader, s.Decision, s.Retention)
	}
	return os.WriteFile(filepath.Join(string(dir), "CENSUS.md"), []byte(b.String()), 0o644)
}
