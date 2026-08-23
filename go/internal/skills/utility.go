package skills

import (
	"crypto/sha1"
	"fmt"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

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
func UpdateSkillUtility(ws, skillID string, success bool, failureReason string) (UtilityUpdate, error) {
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
		UtilityBefore: target.UtilityScore, CircuitBefore: target.CircuitState}

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

	if err := SaveSkill(ws, target); err != nil {
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
	if failureReason != "" {
		ctx["note"] = clipRunes(failureReason, 200)
	}
	// The skill linkage is related_ids, NOT loop_id: filing a subject
	// linkage as a run id invents a run and loses the linkage.
	return rec.EventRelated(eventType, u.SkillName, summary, ctx, "",
		[]string{"skill:" + skillID})
}

func round3(f float64) float64 { return math.RoundToEven(f*1000) / 1000 }

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
func RecordVariantOutcome(ws, skillID string, success bool) error {
	pool := LoadSkills(ws).Skills
	for i := range pool {
		s := pool[i]
		if s.ID != skillID || s.VariantOf == nil {
			continue
		}
		if success {
			s.VariantWins++
		} else {
			s.VariantLosses++
		}
		return SaveSkill(ws, &s)
	}
	return nil
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
	if err := os.MkdirAll(dir, 0o755); err != nil {
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
