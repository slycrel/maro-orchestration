package skills

import (
	"crypto/sha1"
	"fmt"
	"math"
	"math/big"
	"os"
	"path/filepath"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Lifecycle constants (skills.py module level).
const (
	EscalationThreshold = 0.4 // success_rate below this → needs redesign
	UtilityEMAAlpha     = 0.3 // EMA smoothing for utility score

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
	return rec.Event(eventType, u.SkillName, summary, ctx, "skill:"+skillID)
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

// FrontierSkills returns skills with enough HONEST evidence to be worth
// A/B testing — gated on injected_runs, not use_count.
//
// use_count is legacy-frozen: its only writer was removed after it turned
// out to have never had a caller, so it sat at 0 for 312 of 314 live skills
// and silently starved this gate (and the whole variant subsystem behind
// it). Gating on the injected counters is the fix Python landed; the port
// starts there rather than reproducing the dead field's gate.
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
		if s.VariantOf != nil {
			continue // a challenger is not its own frontier candidate
		}
		if st, ok := statsByID[s.ID]; ok && st.InjectedRuns >= minUses {
			out = append(out, s)
		}
	}
	return out
}

// SkillsNeedingEscalation lists skills whose measured success rate has
// fallen below the redesign bar.
func SkillsNeedingEscalation(ws string) []SkillStats {
	var out []SkillStats
	for _, st := range GetAllSkillStats(ws) {
		if st.NeedsEscalation {
			out = append(out, st)
		}
	}
	return out
}

// ProvenanceRecord is one skill-decision audit row.
type ProvenanceRecord struct {
	SkillName       string   `json:"skill_name"`
	Decision        string   `json:"decision"`
	Reason          string   `json:"reason"`
	SuccessRate     float64  `json:"success_rate"`
	EfficiencyScore float64  `json:"efficiency_score"`
	SourceLoopIDs   []string `json:"source_loop_ids"`
	RecordedAt      string   `json:"recorded_at"`
}

// WriteSkillProvenance appends one skill-decision record. Append-only: the
// retention doctrine applies to decisions about skills exactly as it does
// to the skills themselves.
func WriteSkillProvenance(ws string, p ProvenanceRecord) error {
	if p.RecordedAt == "" {
		p.RecordedAt = nowISO()
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
	raw, err := marshalValue(map[string]any{
		"skill_name": p.SkillName, "decision": p.Decision,
		"reason": p.Reason, "success_rate": p.SuccessRate,
		"efficiency_score": p.EfficiencyScore,
		"source_loop_ids":  p.SourceLoopIDs, "recorded_at": p.RecordedAt,
	})
	if err != nil {
		return err
	}
	if _, err := record.LoadsClean(raw); err != nil {
		return fmt.Errorf("emitted provenance line would not be admitted: %w", err)
	}
	path := skillProvenancePath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return record.AppendRawLine(path, []byte(raw))
}

func skillProvenancePath(ws string) string {
	return filepath.Join(ws, "memory", "skill_provenance.jsonl")
}
