package skills

import (
	"crypto/sha1"
	"fmt"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Lifecycle constants (skills.py module level).
const (
	EscalationThreshold = 0.4 // success_rate below this → needs redesign
	UtilityEMAAlpha     = 0.3 // EMA smoothing for utility score

	// Frontier band (Agent0 steal): below LOW is a struggling skill the
	// circuit breaker already owns, above HIGH is a healthy one to leave
	// alone. Between them is the ~50%-solve zone worth experimenting on.
	FrontierLow  = 0.40
	FrontierHigh = 0.70

	// Circuit breaker: network-blip vs structural failure.
	CircuitOpenThreshold    = 3 // consecutive failures to trip closed→open
	CircuitHalfOpenRecovery = 2 // consecutive successes to close half_open→closed
)

// UtilityUpdate reports what one outcome did to a skill, so the caller can
// log the transition without re-reading the store.
type UtilityUpdate struct {
	Found            bool
	SkillName        string
	UtilityBefore    float64
	UtilityAfter     float64
	CircuitBefore    string
	CircuitAfter     string
	ConsecutiveFails int
	ConsecutiveWins  int
	// Warnings carries the rewrite's own announcement (carried-verbatim
	// counts, strandees, ghost ids). It exists because the save moved from
	// SaveSkill to SaveSkills, which HAS an announcement — and dropping it
	// on the floor would make this the one destructive write in the
	// library that rewrites the store silently.
	Warnings []string
	// The inputs to the INPUT_MISMATCH check, carried so the announcement
	// half can run at the log site rather than the store site. Python does
	// both inside update_skill_utility; this port splits store from
	// announcement on purpose (see LogCircuitTransition), and the split is
	// only honest if the log site has everything it needs to say.
	StepText        string
	TriggerPatterns []string
	Success         bool
}

// Changed reports whether the circuit state moved — the condition Python
// logs a captain's-log event on.
func (u UtilityUpdate) Changed() bool { return u.CircuitBefore != u.CircuitAfter }

// UpdateSkillUtility updates a skill's utility EMA and circuit-breaker
// state from one step outcome.
//
// Circuit-breaker state machine:
//
//	closed    → consecutive failures >= CircuitOpenThreshold → open
//	open      → any success → half_open (on probation)
//	half_open → consecutive successes >= CircuitHalfOpenRecovery → closed
//	half_open → another failure → open (trips again immediately)
//	closed    → single failure → stays closed (blip tolerance)
//
// It deliberately does NOT write skill-stats: both live call paths record
// their own outcome with cost/latency, and the internal call this used to
// make double-counted every outcome.
//
// NAMED DIVERGENCE (backport candidate #14): Python captures
// `old_utility` AFTER applying the EMA, so every SKILL_CIRCUIT_* event it
// logs reports utility_before == utility_after. This captures the real
// prior value. The fix is in the reporting only — the stored EMA is
// identical — but a Python log line claiming an unchanged utility across a
// circuit trip is misleading evidence, and evidence is the point.
//
// stepText is the text the skill was invoked ON. It is what lets the
// announcement distinguish "this skill is degrading" from "this skill was
// handed the wrong kind of input"; pass "" when the caller genuinely does
// not have it, which suppresses the INPUT_MISMATCH check exactly as
// Python's `and step_text` guard does.
func UpdateSkillUtility(ws, skillID string, success bool, failureReason, stepText string) (UtilityUpdate, error) {
	pool := LoadSkills(ws).Skills
	var target *Skill
	for i := range pool {
		if pool[i].ID == skillID {
			target = &pool[i]
			break
		}
	}
	if target == nil {
		return UtilityUpdate{}, nil
	}
	up := UtilityUpdate{Found: true, SkillName: target.Name,
		UtilityBefore: target.UtilityScore, CircuitBefore: target.CircuitState,
		StepText: stepText, TriggerPatterns: target.TriggerPatterns,
		Success: success}

	newObs := 0.0
	if success {
		newObs = 1.0
	}
	target.UtilityScore = UtilityEMAAlpha*newObs + (1-UtilityEMAAlpha)*target.UtilityScore

	if success {
		target.ConsecutiveFailures = 0
		target.ConsecutiveSuccesses++
		switch target.CircuitState {
		case "open":
			// First success after open → probationary half-open, and the
			// recovery counter restarts from this success.
			target.CircuitState = "half_open"
			target.ConsecutiveSuccesses = 1
		case "half_open":
			if target.ConsecutiveSuccesses >= CircuitHalfOpenRecovery {
				target.CircuitState = "closed"
			}
		}
	} else {
		target.ConsecutiveSuccesses = 0
		target.ConsecutiveFailures++
		if target.CircuitState == "half_open" {
			target.CircuitState = "open" // failed during recovery
		} else if target.CircuitState == "closed" &&
			target.ConsecutiveFailures >= CircuitOpenThreshold {
			target.CircuitState = "open"
		}
		if failureReason != "" {
			// Keep the five most recent notes, each clipped to 200 runes.
			notes := append(target.FailureNotes, clipRunes(failureReason, 200))
			if len(notes) > 5 {
				notes = notes[len(notes)-5:]
			}
			target.FailureNotes = notes
		}
	}

	up.UtilityAfter = target.UtilityScore
	up.CircuitAfter = target.CircuitState
	up.ConsecutiveFails = target.ConsecutiveFailures
	up.ConsecutiveWins = target.ConsecutiveSuccesses

	// Python recomputes the hash after mutating, then saves through
	// _save_skills(updated_ids={id}) — the ORDINAL-HOLDING rewrite, not
	// save_skill.
	//
	// This used to call SaveSkill, which is the port of Python's other
	// writer: it drops the matching row and appends the new one at the
	// TAIL. pool.go states in its own words why that is wrong here — the
	// store is read last-row-wins by id, so moving a row past its
	// neighbours changes pool ORDER, and pool order is the input to every
	// limit-capped sweep. Measured on one seeded store: after a single
	// outcome on B, Python's order stayed [A B C D] and Go's became
	// [A C D B], and a 2-candidate promotion sweep then promoted [A B] in
	// one runtime and [A C] in the other. This is the library's
	// highest-frequency writer, so it was the invariant's loudest
	// violator (adversarial r3 2026-08-23, H1).
	target.ContentHash = ComputeSkillHash(*target)
	warns, err := SaveSkills(ws, pool, nil, NewIDSet(skillID))
	up.Warnings = warns
	if err != nil {
		return up, err
	}
	return up, nil
}

// LogCircuitTransition writes the captain's-log event for a circuit move.
// Split from the update so the store write and the log write fail
// independently: a log outage must not lose the state change, and the
// caller decides whether a failed log is a warning or fatal.
func LogCircuitTransition(rec *record.Recorder, skillID string, u UtilityUpdate, failureReason string) error {
	if rec == nil || !u.Changed() {
		return nil
	}
	eventType := map[string]string{
		"open":      "SKILL_CIRCUIT_OPEN",
		"half_open": "SKILL_CIRCUIT_HALF_OPEN",
		"closed":    "SKILL_CIRCUIT_CLOSED",
	}[u.CircuitAfter]
	if eventType == "" {
		eventType = "SKILL_CIRCUIT_OPEN"
	}
	summary := fmt.Sprintf("Circuit %s -> %s. Utility: %.2f -> %.2f.",
		u.CircuitBefore, u.CircuitAfter, u.UtilityBefore, u.UtilityAfter)
	ctx := map[string]any{
		"skill_id":              skillID,
		"utility_before":        round3(u.UtilityBefore),
		"utility_after":         round3(u.UtilityAfter),
		"consecutive_failures":  u.ConsecutiveFails,
		"consecutive_successes": u.ConsecutiveWins,
	}
	// The note is a TOP-LEVEL row key, not a context entry: Python's
	// render_entry reads entry["note"] and prints a "Note:" line, and
	// nothing renders context values — so filed under context the failure
	// reason survived search and vanished from every human-facing render,
	// which is the one thing a circuit event exists to carry.
	note := ""
	if failureReason != "" {
		note = clipRunes(failureReason, 200)
	}
	// The skill linkage is related_ids, NOT loop_id: filing a subject
	// linkage as a run id invents a run and loses the linkage.
	if err := rec.EventNoted(eventType, u.SkillName, summary, ctx, note, "",
		[]string{"skill:" + skillID}); err != nil {
		return err
	}
	return logInputMismatch(rec, skillID, u)
}

// logInputMismatch is the second half of the circuit-open announcement.
//
// When a circuit trips and the text the skill was handed contradicts the
// skill's own trigger vocabulary — a web-fetch skill given plain prose, or
// a prose skill given a URL — Python says so, with a note reading
// "Inspector: treat this as INPUT_MISMATCH, not skill degradation." This
// port opened the circuit and stopped there (adversarial r4, M2), which is
// r3's pattern 2 again: the DECISION ports faithfully and the
// ANNOUNCEMENT of it does not. A store-level differential cannot see it,
// because the stores agree.
//
// INPUT_MISMATCH is a SYSTEM-audience event in Python (it is absent from
// USER_SURFACED_EVENTS), and it has no automated consumer — its whole
// value is telling a human reading `maro-log` that the trip is a domain
// mismatch rather than skill rot.
func logInputMismatch(rec *record.Recorder, skillID string, u UtilityUpdate) error {
	// Python's guard: the circuit must have just moved INTO open, the
	// outcome must be a failure, and there must be step text at all.
	if u.CircuitAfter != "open" || u.CircuitBefore == "open" ||
		u.Success || u.StepText == "" {
		return nil
	}
	inputType := record.ClassifyInputType(u.StepText)
	triggerText := pytext.Lower(strings.Join(u.TriggerPatterns, " "))
	urlSkill := false
	for _, kw := range []string{"url", "web", "http", "jina", "fetch", "scrape"} {
		if strings.Contains(triggerText, kw) {
			urlSkill = true
			break
		}
	}
	urlInput := inputType == "url"
	if urlSkill == urlInput {
		return nil // vocabulary and input agree: an ordinary degradation
	}
	expects := "non-url"
	if urlSkill {
		expects = "url"
	}
	summary := fmt.Sprintf("Skill '%s' expects %s input but received %s. "+
		"Circuit opened — failures may reflect domain mismatch.",
		u.SkillName, expects, pytext.Repr(inputType))
	return rec.EventNoted("INPUT_MISMATCH", u.SkillName, summary,
		map[string]any{
			"skill_id":             skillID,
			"input_type":           inputType,
			"skill_url_domain":     urlSkill,
			"consecutive_failures": u.ConsecutiveFails,
		},
		"Inspector: treat this as INPUT_MISMATCH, not skill degradation.",
		"", []string{"skill:" + skillID})
}

// round3 is Python's round(f, 3).
//
// The obvious spelling — RoundToEven(f*1000)/1000 — rounds the PRODUCT,
// which carries its own representation error, where Python rounds the
// exact value of the double. Over the 400 three-decimal half-values
// 0.0005…0.3995, 202 diverge: round3(0.6675) gave 0.668 where Python
// gives 0.667. Formatting to decimal and parsing back is the same
// decimal-correct rounding Python does, and Go's own %.3f already agrees
// with it — which is how the bug showed itself, as a SKILL_DEMOTED row
// carrying "utility_score=0.877 < 0.4" in its reason next to
// context.utility: 0.878, two spellings of one number in one event
// (adversarial r3 2026-08-23, L4).
//
// Reach, measured honestly: walking all 4,194,304 EMA-reachable values
// 22 steps deep from the default utility_score of 1.0 found ZERO
// divergences, so the ordinary EMA path was never affected. It is
// reachable through any STORED score — a pack import, a hand-edited row,
// a variant inheriting a value — which goes straight into the
// promotion and demotion events.
func round3(f float64) float64 { return pyRound(f, 3) }

// pyRound is round(f, n) for the digit counts this package uses.
func pyRound(f float64, n int) float64 {
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return f
	}
	out, err := strconv.ParseFloat(strconv.FormatFloat(f, 'f', n, 64), 64)
	if err != nil {
		return f
	}
	return out
}

func clipRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}

// ---------------------------------------------------------------------------
// A/B variants
// ---------------------------------------------------------------------------

// GetSkillVariants returns the active challengers for a parent id.
func GetSkillVariants(parentID string, pool []Skill) []Skill {
	var out []Skill
	for _, s := range pool {
		if s.VariantOf != nil && *s.VariantOf == parentID {
			out = append(out, s)
		}
	}
	return out
}

// SelectVariantForTask routes between a parent and its challengers using a
// hash of taskID, so the same task always lands on the same arm. Returns
// the parent unchanged when no variants exist.
//
// The hash is the FULL sha1 digest read as a big integer mod pool size —
// Python's `int(sha1(...).hexdigest(), 16) % len(pool)`. Using a truncated
// prefix would route differently, and the arms must agree across runtimes
// or the A/B evidence is not comparable.
func SelectVariantForTask(parent Skill, taskID string, pool []Skill) Skill {
	variants := GetSkillVariants(parent.ID, pool)
	if len(variants) == 0 {
		return parent
	}
	arms := append([]Skill{parent}, variants...)
	sum := sha1.Sum([]byte(taskID))
	n := new(big.Int).SetBytes(sum[:])
	bucket := new(big.Int).Mod(n, big.NewInt(int64(len(arms)))).Int64()
	return arms[bucket]
}

// RecordVariantOutcome credits a win or loss to a challenger. A no-op for
// non-variant skills — a parent's record is its own stats, not an arm.
//
// Like UpdateSkillUtility this saves through the ordinal-holding rewrite
// (Python: _save_skills(updated_ids={id})), NOT through SaveSkill, so an
// A/B outcome cannot reorder the pool underneath a capped sweep.
//
// It deliberately does NOT recompute content_hash, and the asymmetry with
// UpdateSkillUtility is Python's: _save_skills backfills only an EMPTY
// hash for a named write, so a stored hash that disagrees with its skill
// SURVIVES here and keeps warning on every load. Routing this through
// SaveSkill silently recomputed it, so one A/B win permanently erased the
// tamper-detection signal with nothing announced (adversarial r3, L1).
func RecordVariantOutcome(ws, skillID string, success bool) ([]string, error) {
	pool := LoadSkills(ws).Skills
	for i := range pool {
		if pool[i].ID != skillID || pool[i].VariantOf == nil {
			continue
		}
		// Mutate IN the pool: taking a copy first meant the saved row and
		// the pool the rewrite was built from disagreed.
		if success {
			pool[i].VariantWins++
		} else {
			pool[i].VariantLosses++
		}
		return SaveSkills(ws, pool, nil, NewIDSet(skillID))
	}
	return nil, nil
}

// FrontierSkills returns the skills worth A/B testing: those whose HONEST
// evidence puts them in the frontier BAND — neither reliably solved nor
// reliably failing — sorted hardest first.
//
// The band is the whole point (Agent0's R_unc: target the ~50%-solve zone).
// An earlier version of this port kept only the injected_runs gate, which
// handed the evolver every skill with enough runs — 100%-success skills
// included — in map order. Three further halves were missing with it: the
// open-circuit skip (those are skills_needing_rewrite's job, not a variant
// experiment's), the ascending sort, and the requirement that a challenger
// IS eligible. Excluding challengers looked principled and is not what
// Python does; a challenger that lands mid-band is exactly the thing worth
// splitting again.
//
// The runs gate itself is honest evidence only: use_count is legacy-frozen
// (its only writer was removed after it turned out to have never had a
// caller), so it sat at 0 for 312 of 314 live skills and starved the whole
// variant subsystem.
func FrontierSkills(ws string, pool []Skill, minUses int) []Skill {
	if minUses <= 0 {
		minUses = 3
	}
	statsByID := map[string]SkillStats{}
	for _, st := range GetAllSkillStats(ws) {
		statsByID[st.SkillID] = st
	}
	var out []Skill
	for _, s := range pool {
		st, ok := statsByID[s.ID]
		if !ok || st.InjectedRuns < minUses {
			continue
		}
		if s.CircuitState == "open" {
			continue // sustained failure is a rewrite, not an experiment
		}
		if st.InjectedSuccessRate >= FrontierLow && st.InjectedSuccessRate <= FrontierHigh {
			out = append(out, s)
		}
	}
	// Hardest first. Stable, so equal rates keep pool order in both
	// runtimes — Python's sorted() is stable and this decides which skills
	// an evolver pass spends its budget on.
	sort.SliceStable(out, func(i, j int) bool {
		return statsByID[out[i].ID].InjectedSuccessRate <
			statsByID[out[j].ID].InjectedSuccessRate
	})
	return out
}

// SkillsNeedingEscalation lists skills whose measured success rate has
// fallen below the redesign bar.
//
// It RECOMPUTES from success_rate rather than reading the stored
// needs_escalation flag, because the flag and the rate drift apart by
// design: record_skill_injection_outcomes deliberately does not recompute
// it, and a legacy row written before the field existed defaults to false.
// Reading the flag returned a set DISJOINT from Python's on the same store
// — a stale-true row with a healthy rate flagged, and every low-rate legacy
// row missed, which is the one case the redesign bar exists for.
func SkillsNeedingEscalation(ws string) []SkillStats {
	var out []SkillStats
	for _, st := range GetAllSkillStats(ws) {
		if st.SuccessRate < EscalationThreshold {
			out = append(out, st)
		}
	}
	return out
}

// ProvenanceField is one extra key/value, carried as an ORDERED list rather
// than a map: this record is rendered with its keys in insertion order to
// match Python's dict, and a Go map would order them randomly per run.
type ProvenanceField struct {
	Key   string
	Value any
}

// ProvenanceRecord is one skill-decision audit record.
type ProvenanceRecord struct {
	SkillName       string
	Decision        string // promote | demote | rewrite | create | retire | delete
	Reason          string
	SuccessRate     float64
	EfficiencyScore float64
	SourceLoopIDs   []string
	DecidedAt       string
	Extra           []ProvenanceField
}

// WriteSkillProvenance writes one skill-decision record as a SIDECAR file,
// exactly where Python's write_skill_provenance puts it:
// memory/skill_provenance/{skill_name}_{stamp}.json, indent-2 JSON, keys in
// Python's insertion order.
//
// The store SHAPE is the interop contract, not just the field names. An
// earlier version of this port appended rows to a single
// memory/skill_provenance.jsonl — which no Python reader ever opens, since
// load_skill_provenance globs the sidecar directory. Every Go cull and
// demotion would have filed its reasoning into a file the other runtime
// cannot see, while Python's audit answered "no provenance" for skills this
// runtime had retired with a documented reason.
//
// Two named divergences, both refusals where Python fails silently:
//
//   - A skill name carrying a path separator or a leading dot would escape
//     the provenance directory. Python builds the same path and its write
//     raises into a debug-level except, so the record vanishes with no
//     operator-visible signal; here it is an error the caller announces.
//   - decided_at uses the port-wide six-digit stamp. Python's isoformat()
//     omits the fraction entirely when the microsecond happens to be zero,
//     a ~1e-6 event that changes the string, not the instant.
//
// Same-microsecond records for one skill collide on the filename and the
// second wins — inherited from Python, and the reason the filename carries
// microseconds at all.
func WriteSkillProvenance(ws string, p ProvenanceRecord) error {
	if p.DecidedAt == "" {
		p.DecidedAt = nowISO()
	}
	if p.SourceLoopIDs == nil {
		p.SourceLoopIDs = []string{}
	}
	for _, v := range append([]string{p.SkillName, p.Decision, p.Reason},
		p.SourceLoopIDs...) {
		if !isCleanText(v) {
			return fmt.Errorf("provenance record carries byte-tainted text")
		}
	}
	if p.SkillName == "" || strings.ContainsAny(p.SkillName, `/\`) ||
		strings.HasPrefix(p.SkillName, ".") {
		return fmt.Errorf("provenance refused: skill name %s cannot be a filename",
			pyRepr(p.SkillName))
	}

	fields := []ProvenanceField{
		{"skill_name", p.SkillName},
		{"decision", p.Decision},
		{"reason", p.Reason},
		{"decided_at", p.DecidedAt},
		{"success_rate", p.SuccessRate},
		{"efficiency_score", p.EfficiencyScore},
		{"source_loop_ids", p.SourceLoopIDs},
	}
	// Python spreads `**extra` last, so an extra key repeating a modeled one
	// REPLACES its value while keeping the modeled key's position.
	for _, e := range p.Extra {
		replaced := false
		for i := range fields {
			if fields[i].Key == e.Key {
				fields[i].Value = e.Value
				replaced = true
				break
			}
		}
		if !replaced {
			fields = append(fields, e)
		}
	}
	raw, err := pyJSONIndent(fields, 2)
	if err != nil {
		return err
	}

	dir := filepath.Join(ws, "memory", "skill_provenance")
	if err := os.MkdirAll(dir, record.NewDirMode); err != nil {
		return err
	}
	stamp, err := time.Parse("2006-01-02T15:04:05.000000-07:00", p.DecidedAt)
	if err != nil {
		// A caller-supplied stamp this cannot parse must not silently
		// produce a filename that sorts wrong — load_skill_provenance
		// orders records by FILENAME.
		return fmt.Errorf("provenance decided_at %s is not the port stamp: %w",
			pyRepr(p.DecidedAt), err)
	}
	// strftime's %f is six digits with no separator. The point is removed
	// from the STAMP alone — a skill name may legitimately hold one
	// ("http-fetch-v1.2"), and stripping the first point in the joined
	// string would corrupt the name half.
	micros := strings.Replace(stamp.UTC().Format("150405.000000"), ".", "", 1)
	name := p.SkillName + "_" + stamp.UTC().Format("20060102T") + micros + "Z"
	return record.AtomicWrite(filepath.Join(dir, name+".json"), []byte(raw))
}

// pyJSONIndent renders ordered fields the way json.dumps(obj, indent=n)
// does. Written out rather than delegated to encoding/json because that
// package sorts map keys, escapes HTML, and spells a whole float without
// its ".0" — three separate ways to produce a record Python renders
// differently.
func pyJSONIndent(fields []ProvenanceField, indent int) (string, error) {
	pad := strings.Repeat(" ", indent)
	var sb strings.Builder
	sb.WriteString("{\n")
	for i, f := range fields {
		key, err := jsonString(f.Key)
		if err != nil {
			return "", err
		}
		val, err := pyJSONValue(f.Value, pad, indent)
		if err != nil {
			return "", err
		}
		sb.WriteString(pad + key + ": " + val)
		if i < len(fields)-1 {
			sb.WriteByte(',')
		}
		sb.WriteByte('\n')
	}
	sb.WriteByte('}')
	return sb.String(), nil
}

// pyJSONValue renders one value at the given nesting. An EMPTY list is "[]"
// on one line (Python's indent writer emits no newline for an empty
// container); a non-empty one puts each element on its own line.
func pyJSONValue(v any, pad string, indent int) (string, error) {
	items, isList := v.([]string)
	if !isList {
		if anyList, ok := v.([]any); ok {
			for _, e := range anyList {
				items = append(items, pyStr(e))
			}
			isList = true
		}
	}
	if isList {
		if len(items) == 0 {
			return "[]", nil
		}
		inner := pad + strings.Repeat(" ", indent)
		var sb strings.Builder
		sb.WriteString("[\n")
		for i, s := range items {
			enc, err := jsonString(s)
			if err != nil {
				return "", err
			}
			sb.WriteString(inner + enc)
			if i < len(items)-1 {
				sb.WriteByte(',')
			}
			sb.WriteByte('\n')
		}
		sb.WriteString(pad + "]")
		return sb.String(), nil
	}
	return marshalValue(v)
}
