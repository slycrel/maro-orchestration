// Package skills ports the skill library (Python skills.py + skill_types.py):
// reusable execution patterns extracted from completed runs, injected into
// future planning prompts when a goal matches.
//
// Slice 3a is the STORE + RETRIEVAL half — the part that makes a skill reach
// a run: load/save/archive with the full byte-hygiene posture, and the
// three-tier match (router → keyword → TF-IDF fallback). The lifecycle half
// (outcome recording, utility/circuit updates, promote/demote, variants,
// island culls, test gate) lands in slice 3b.
//
// Lessons are data: skills live as JSONL rows in the shared workspace, so a
// skill minted by either runtime is readable by the other. That makes the
// admission predicate — what a row must prove before it is admitted as a
// Skill — the load-bearing contract, not the struct.
package skills

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Skill mirrors skill_types.Skill. Field ORDER matches Python's
// skill_to_dict so the emitted row reads identically in both runtimes.
type Skill struct {
	ID                    string         `json:"id"`
	Name                  string         `json:"name"`
	Description           string         `json:"description"`
	TriggerPatterns       []string       `json:"trigger_patterns"`
	StepsTemplate         []string       `json:"steps_template"`
	SourceLoopIDs         []string       `json:"source_loop_ids"`
	CreatedAt             string         `json:"created_at"`
	UseCount              int            `json:"use_count"`
	SuccessRate           float64        `json:"success_rate"`
	ContentHash           string         `json:"content_hash"`
	Tier                  string         `json:"tier"`
	UtilityScore          float64        `json:"utility_score"`
	FailureNotes          []string       `json:"failure_notes"`
	ConsecutiveFailures   int            `json:"consecutive_failures"`
	ConsecutiveSuccesses  int            `json:"consecutive_successes"`
	CircuitState          string         `json:"circuit_state"`
	OptimizationObjective string         `json:"optimization_objective"`
	Island                string         `json:"island"`
	VariantOf             *string        `json:"variant_of"`
	VariantWins           int            `json:"variant_wins"`
	VariantLosses         int            `json:"variant_losses"`
	Project               string         `json:"project"`
	Imported              map[string]any `json:"imported"`
	Origin                string         `json:"origin"`
	Domain                string         `json:"domain"`
	Tags                  []string       `json:"tags"`

	// Match-tier telemetry. Python stamps these as DYNAMIC attributes, so
	// they are never serialized — json:"-" is the port's way of saying the
	// same thing. "router" | "keyword" | "tfidf_fallback".
	MatchMethod string  `json:"-"`
	MatchScore  float64 `json:"-"`
}

// newSkill returns a Skill carrying dict_to_skill's defaults, so a partial
// row constructs exactly as Python's does.
func newSkill() Skill {
	return Skill{
		TriggerPatterns: []string{}, StepsTemplate: []string{},
		SourceLoopIDs: []string{}, FailureNotes: []string{}, Tags: []string{},
		SuccessRate: 1.0, Tier: "provisional", UtilityScore: 1.0,
		CircuitState: "closed", Imported: map[string]any{},
	}
}

// NormalizeTags ports skill_types.normalize_tags: ONE normalizer for every
// tag boundary. List-only (a string value would otherwise iterate into
// character tags that keyword-match everything), lowercased, stripped,
// empties dropped, capped at mint. cap < 0 means no cap — the read path,
// where stored rows must not be re-truncated.
func NormalizeTags(raw any, cap int) []string {
	list, ok := raw.([]any)
	if !ok {
		return []string{}
	}
	out := []string{}
	for _, t := range list {
		s := strings.TrimSpace(pyStr(t))
		if s == "" {
			continue
		}
		out = append(out, strings.ToLower(s))
	}
	if cap >= 0 && len(out) > cap {
		out = out[:cap]
	}
	return out
}

// ComputeSkillHash ports compute_skill_hash: SHA256 over the content fields
// (name, description, steps, objective) joined by newlines.
func ComputeSkillHash(s Skill) string {
	content := strings.Join([]string{
		s.Name, s.Description, strings.Join(s.StepsTemplate, "\n"),
		s.OptimizationObjective,
	}, "\n")
	sum := sha256.Sum256([]byte(content))
	return hex.EncodeToString(sum[:])
}

// VerifySkillHash reports whether the content matches a recorded hash. An
// empty expectation verifies (nothing was claimed).
func VerifySkillHash(s Skill, expected string) bool {
	if expected == "" {
		return true
	}
	return ComputeSkillHash(s) == expected
}

// DictToSkill ports dict_to_skill: a tolerant CONSTRUCTOR for read paths.
// Unknown keys ignored, missing keys defaulted, numeric-ish fields coerced.
// It proves nothing — callers that REMOVE rows must use ValidateSkillRow.
func DictToSkill(d map[string]any) (Skill, error) {
	for _, k := range []string{"id", "name", "description"} {
		if _, ok := d[k]; !ok {
			return Skill{}, fmt.Errorf("missing required key %q", k)
		}
	}
	s := newSkill()
	s.ID = pyStr(d["id"])
	s.Name = pyStr(d["name"])
	s.Description = pyStr(d["description"])
	strList(d, "trigger_patterns", &s.TriggerPatterns)
	strList(d, "steps_template", &s.StepsTemplate)
	strList(d, "source_loop_ids", &s.SourceLoopIDs)
	strList(d, "failure_notes", &s.FailureNotes)
	getStr(d, "created_at", &s.CreatedAt)
	getInt(d, "use_count", &s.UseCount)
	getFloat(d, "success_rate", &s.SuccessRate)
	getStr(d, "content_hash", &s.ContentHash)
	getStr(d, "tier", &s.Tier)
	getFloat(d, "utility_score", &s.UtilityScore)
	getInt(d, "consecutive_failures", &s.ConsecutiveFailures)
	getInt(d, "consecutive_successes", &s.ConsecutiveSuccesses)
	getStr(d, "circuit_state", &s.CircuitState)
	getStr(d, "optimization_objective", &s.OptimizationObjective)
	getStr(d, "island", &s.Island)
	if v, ok := d["variant_of"]; ok && v != nil {
		vs := pyStr(v)
		s.VariantOf = &vs
	}
	getInt(d, "variant_wins", &s.VariantWins)
	getInt(d, "variant_losses", &s.VariantLosses)
	getStr(d, "project", &s.Project)
	if m, ok := d["imported"].(map[string]any); ok {
		s.Imported = m
	}
	getStr(d, "origin", &s.Origin)
	// Origin derivation-at-read: an imported dict is certain evidence of
	// pack import, so blank-origin legacy rows with one get "imported" for
	// free. Everything else stays "" — crystallized vs synthesized is not
	// reliably derivable retroactively, and guessing would violate
	// positive-evidence.
	if s.Origin == "" && len(s.Imported) > 0 {
		s.Origin = "imported"
	}
	getStr(d, "domain", &s.Domain)
	s.Tags = NormalizeTags(d["tags"], -1)
	return s, nil
}

// skillKeyOrder is skill_to_dict()'s key order — the order Python's
// json.dumps emits, and therefore the order a row rewritten by either
// runtime must read in.
var skillKeyOrder = []string{"id", "name", "description", "trigger_patterns",
	"steps_template", "source_loop_ids", "created_at", "use_count",
	"success_rate", "content_hash", "tier", "utility_score", "failure_notes",
	"consecutive_failures", "consecutive_successes", "circuit_state",
	"optimization_objective", "island", "variant_of", "variant_wins",
	"variant_losses", "project", "imported", "origin", "domain", "tags"}

// MarshalJSON emits the row through ToDict rather than the struct tags, so
// the key set and order have ONE definition and float fields get Python's
// spelling (success_rate 1.0, not 1 — see marshalValue). Encoding the
// struct directly kept a second, silently-drifting copy of the same
// contract.
func (s Skill) MarshalJSON() ([]byte, error) {
	line, err := marshalOrdered(s.ToDict(), skillKeyOrder)
	if err != nil {
		return nil, err
	}
	return []byte(line), nil
}

// ToDict ports skill_to_dict — the exact key set Python writes.
func (s Skill) ToDict() map[string]any {
	return map[string]any{
		"id": s.ID, "name": s.Name, "description": s.Description,
		"trigger_patterns": s.TriggerPatterns, "steps_template": s.StepsTemplate,
		"source_loop_ids": s.SourceLoopIDs, "created_at": s.CreatedAt,
		"use_count": s.UseCount, "success_rate": s.SuccessRate,
		"content_hash": s.ContentHash, "tier": s.Tier,
		"utility_score": s.UtilityScore, "failure_notes": s.FailureNotes,
		"consecutive_failures":   s.ConsecutiveFailures,
		"consecutive_successes":  s.ConsecutiveSuccesses,
		"circuit_state":          s.CircuitState,
		"optimization_objective": s.OptimizationObjective,
		"island":                 s.Island, "variant_of": s.VariantOf,
		"variant_wins": s.VariantWins, "variant_losses": s.VariantLosses,
		"project": s.Project, "imported": s.Imported,
		"origin": s.Origin, "domain": s.Domain, "tags": s.Tags,
	}
}

// strFields / listOfStrFields mirror skill_types' explicit field lists.
// Named explicitly rather than derived from the struct tags for the same
// reason Python does not derive them from annotations: the point of the
// validator is that the declaration is NOT enforced by the language, and
// deriving the list from the declaration would re-inherit that gap. Python
// r4 found the first cut short by tier, failure_notes and tags — each one a
// field a forged row could carry junk in and still be admitted.
var strFields = []string{"id", "name", "description", "content_hash",
	"created_at", "tier", "circuit_state", "optimization_objective",
	"island", "domain", "project", "origin"}

var listOfStrFields = []string{"trigger_patterns", "steps_template",
	"source_loop_ids", "failure_notes", "tags"}

// ValidateSkillRow builds a Skill from a stored row AND proves the row is
// one. It checks the STORED value, never what the constructor made of it:
// DictToSkill coerces (that is right for a tolerant read path), and a
// caller about to DELETE rows must prove what the store says.
//
// The rule this enforces, from Python's arc of six adversarial rounds: a
// row that cannot be PROVEN to be a Skill must never take part in a
// decision about which rows to remove — strand the raw line instead. A
// forged row carrying a healthy skill's content_hash but description=7
// could not have its hash recomputed, counted as "not stale", won the dedup
// on a later created_at, and DELETED the healthy skill.
//
// content_hash and created_at absence is an ABSENCE OF PROOF, not a
// default: both are fields the destructive caller ACTS ON, and both are
// excluded from the dedup identity, so neither absence shows up as a
// difference.
func ValidateSkillRow(d map[string]any) (Skill, error) {
	for _, name := range []string{"id", "name", "description"} {
		if _, ok := d[name]; !ok {
			return Skill{}, fmt.Errorf("missing required key %q", name)
		}
	}
	for _, name := range []string{"content_hash", "created_at"} {
		if _, ok := d[name]; !ok {
			return Skill{}, fmt.Errorf("%s is required to take part in a "+
				"removal decision (absent is not empty, and neither is proof)", name)
		}
	}
	for _, name := range strFields {
		if v, ok := d[name]; ok {
			if _, isStr := v.(string); !isStr {
				return Skill{}, fmt.Errorf("%s must be a string, got %#v", name, v)
			}
		}
	}
	for _, name := range []string{"id", "name", "content_hash"} {
		if v, ok := d[name]; ok {
			if strings.TrimSpace(v.(string)) == "" {
				return Skill{}, fmt.Errorf("%s must not be empty", name)
			}
		}
	}
	if v, ok := d["created_at"]; ok { // a timestamp is a RANKING input
		if !isISOTimestamp(v.(string)) {
			return Skill{}, fmt.Errorf("created_at is not a timestamp: %q", v)
		}
	}
	for _, name := range listOfStrFields {
		if v, ok := d[name]; ok {
			list, isList := v.([]any)
			if !isList {
				return Skill{}, fmt.Errorf("%s must be a list of strings", name)
			}
			for _, e := range list {
				if _, isStr := e.(string); !isStr {
					return Skill{}, fmt.Errorf("%s must be a list of strings", name)
				}
			}
		}
	}
	for _, name := range []string{"success_rate", "utility_score"} {
		if v, ok := d[name]; ok {
			f, isNum := numberOf(v)
			if !isNum || math.IsNaN(f) || math.IsInf(f, 0) {
				return Skill{}, fmt.Errorf("%s must be a finite number, got %#v", name, v)
			}
		}
	}
	for _, name := range []string{"use_count", "consecutive_failures",
		"consecutive_successes", "variant_wins", "variant_losses"} {
		if v, ok := d[name]; ok {
			if !isIntValue(v) {
				return Skill{}, fmt.Errorf("%s must be an int, got %#v", name, v)
			}
		}
	}
	if v, ok := d["variant_of"]; ok && v != nil {
		if _, isStr := v.(string); !isStr {
			return Skill{}, fmt.Errorf("variant_of must be a string or null")
		}
	}
	if v, ok := d["imported"]; ok {
		if _, isMap := v.(map[string]any); !isMap {
			return Skill{}, fmt.Errorf("imported must be an object")
		}
	}
	skill, err := DictToSkill(d)
	if err != nil {
		return Skill{}, err
	}
	ComputeSkillHash(skill) // proves the content fields ENCODE
	return skill, nil
}

// proveLine serializes one skill AND proves the next read will admit the
// line. Python's twin exists because both writers emitted rows their own
// reader refuses (a NaN utility_score wrote the CPython token `NaN`; a lone
// surrogate in `tier` — a field the hash never touches — serialized as a
// clean-looking escape), and either way the save DELETED the prior valid
// row and replaced it with one the reader strands: the skill vanished from
// the live pool while its bytes sat on disk. Refusing HERE aborts the save
// before the store is touched.
//
// Go needs this MORE than Python, not less: encoding/json rejects NaN/Inf
// (matching allow_nan=False) but silently rewrites invalid UTF-8 in a
// string to U+FFFD, so the taint would be laundered at the writer with no
// error at all. record.LoadsClean is the reader's predicate, so the writer
// proves against exactly it.
func proveLine(s Skill) (string, error) {
	line, row, err := proveRecordLine(s)
	if err != nil {
		return "", err
	}
	// The COMPLETE admission predicate, not just the byte door: a
	// constructible Skill with tier=7 (hash-excluded, JSON-clean) was
	// emitted by Python, REPLACED the healthy row, and stranded on the
	// next load. A writer into the LIVE store proves what its reader will
	// ADMIT, and the live reader admits via ValidateSkillRow.
	if _, err := ValidateSkillRow(row); err != nil {
		return "", fmt.Errorf("emitted line would not validate: %w", err)
	}
	return line, nil
}

// proveRecordLine is jsonl_utils.prove_record_line for a skill: it proves
// the strict READER admits the line (clean bytes, parseable, an object) and
// nothing more. This is the archive's proof, deliberately weaker than
// proveLine's: the archive is a retention store, not a keyed one, so
// demanding full Skill validity there would let a hash-less or otherwise
// unprovable skill REFUSE its own retention copy — and since the archive
// gates the live-pool removal, an over-strict proof would block the cull
// (or, worse for a caller that swallows the error, delete without a copy).
// Retention must accept everything the live pool can hold.
func proveRecordLine(s Skill) (string, map[string]any, error) {
	// Python's dataclass gives every list field default_factory=list and
	// imported an empty dict, so skill_to_dict NEVER emits null for them.
	// A Go caller building a Skill literal leaves those nil, and nil
	// marshals to `null` — a row BOTH runtimes' validators then refuse
	// ("must be a list of strings"). Normalizing here rather than at each
	// caller keeps the emitted row's VALUES identical to Python's by
	// construction; doing it in the caller would be a footgun with 30-odd
	// call sites.
	//
	// Values, not bytes: Python writes json.dumps' default separators
	// (`", "` / `": "`) and every Go store in this port writes compact
	// JSON, so rows from the two runtimes are visibly different text in the
	// same file. Nothing consumes that difference — Python's dedup identity
	// re-serializes the PARSED row (doctor._dedup_identity, sort_keys=True),
	// which is why the float SPELLING mattered and the spacing does not —
	// but it is a port-wide divergence, not a skills one, and it belongs to
	// a pass over every emitter rather than to this file.
	s.TriggerPatterns = orEmpty(s.TriggerPatterns)
	s.StepsTemplate = orEmpty(s.StepsTemplate)
	s.SourceLoopIDs = orEmpty(s.SourceLoopIDs)
	s.FailureNotes = orEmpty(s.FailureNotes)
	s.Tags = orEmpty(s.Tags)
	if s.Imported == nil {
		s.Imported = map[string]any{}
	}
	for _, v := range []string{s.ID, s.Name, s.Description, s.Tier,
		s.CircuitState, s.OptimizationObjective, s.Island, s.Domain,
		s.Project, s.Origin, s.CreatedAt, s.ContentHash} {
		if !isCleanText(v) {
			return "", nil, fmt.Errorf("skill %s carries byte-tainted text — refusing to write", s.ID)
		}
	}
	for _, list := range [][]string{s.TriggerPatterns, s.StepsTemplate,
		s.SourceLoopIDs, s.FailureNotes, s.Tags} {
		for _, v := range list {
			if !isCleanText(v) {
				return "", nil, fmt.Errorf("skill %s carries byte-tainted text — refusing to write", s.ID)
			}
		}
	}
	var buf strings.Builder
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(s); err != nil { // rejects NaN/Inf like allow_nan=False
		return "", nil, err
	}
	line := strings.TrimSuffix(buf.String(), "\n")
	row, err := record.LoadsClean(line)
	if err != nil {
		return "", nil, fmt.Errorf("emitted line would not be admitted: %w", err)
	}
	return line, row, nil
}
