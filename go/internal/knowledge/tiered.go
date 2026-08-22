// Tiered-lesson retrieval — the read side of the store this package's
// append side already writes (recall tranche).
//
// Ports knowledge_web.load_tiered_lessons with its fix history, not just
// its shape (PORT.md doctrine):
//
//   - Decay is a READ-TIME derivation: the stored score is the score as
//     of last_reinforced, and the effective score is stored *
//     DecayFactor^days. Stored scores are never overwritten with decayed
//     values — that would compound decay on every rewrite. Only MEDIUM
//     decays; LONG is promoted-permanent by design.
//   - Per-row parse guard: one malformed / type-drifted / byte-torn row
//     skips THAT row and increments the skipped count — it never takes
//     the tier down (Python 2026-08-17: a strict whole-file read raised
//     on one crash-torn byte and the tier read as empty; worse, rewrites
//     rebuilt from that empty read). The skipped count is returned so a
//     short read is distinguishable from a short store.
//   - Numeric fields are coerced inside the guard (Python r3/r4 of the
//     same arc: a `"score": "high"` row raised out of the sort and took
//     every read-modify-write on the tier down, while the ordinary read
//     path hid the row).
//
// Named divergence (Go stricter, refusal direction): Python's dataclass
// accepts any type in its string fields (a numeric `lesson` survives
// construction and breaks later, deep inside the ranker); Go skips the
// row through the same skipped-count channel instead.
package knowledge

import (
	"encoding/json"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

// Tier vocabulary and decay/ranking constants, verbatim from
// knowledge_web.py (MemoryTier, DECAY_FACTOR, _CITATION_PENALTY).
const (
	TierShort  = "short"
	TierMedium = "medium"
	TierLong   = "long"

	// DecayFactor is the daily non-reinforced decay multiplier.
	DecayFactor = 0.85
	// CitationPenalty multiplies the ranker score of lessons without
	// evidence_sources (Phase 60: cited lessons rank higher on ties).
	CitationPenalty = 0.90
)

// DaysSince ports knowledge_web._days_since: whole days elapsed since
// the YYYY-MM-DD prefix of dateStr, clamped at 0; unparseable → 0.
func DaysSince(dateStr string) int {
	if len(dateStr) < 10 {
		return 0
	}
	rec, err := time.ParseInLocation("2006-01-02", dateStr[:10], time.UTC)
	if err != nil {
		return 0
	}
	days := int(time.Now().UTC().Sub(rec).Hours() / 24)
	if days < 0 {
		return 0
	}
	return days
}

// DecayScore applies exponential decay: score * DecayFactor^days.
// math.Pow, not iterative multiply — CPython's `0.85 ** days` goes
// through libm pow, and the fixture-parity pins compare these floats.
func DecayScore(score float64, days int) float64 {
	return score * math.Pow(DecayFactor, float64(days))
}

// IsQuarantined ports knowledge_web._is_quarantined: prompt-derived
// lessons are quarantined from every injection surface (the
// lesson_provenance gate answering the db37d525 contamination).
func IsQuarantined(tl TieredLesson) bool { return tl.MintedFrom == "prompt" }

// IsContested ports _is_contested: the dict carries the audit trail;
// emptiness is the flag.
func IsContested(tl TieredLesson) bool { return len(tl.Contested) > 0 }

// LoadOptions mirror load_tiered_lessons' keyword filters. Limit <= 0
// means unlimited (Python's limit=None) — the ZERO VALUE must degrade
// to "everything", never to "nothing": an earlier draft used Limit < 0
// for unlimited, which made LoadOptions{} silently return an empty
// result set indistinguishable from an empty store (adversarial recall
// r1 2026-08-22, Skeptic HIGH — the zero-value footgun sat one field
// away from every caller). Same idiom as budget.Clip's "a breaker that
// is off is off". Named consequence (r2, Skeptic — accepted, not
// fixed): unlike Python's Optional[int], there is NO way to request
// literally zero rows, and a computed limit of 0 means unlimited.
// Deliberate — zero-value safety outranks an unused capability; a
// future caller needing "exactly zero" is asking the wrong function.
// Raw skips the decay derivation AND the MinScore filter — exactly
// Python's ordering, where the min_score check sits behind `not raw`.
type LoadOptions struct {
	TaskType   string
	LessonType string
	MinScore   float64
	Limit      int
	Raw        bool
	MaxAgeDays int // <=0 = no age filter (Python's None)
}

// LoadTieredLessons reads memory/<tier>/lessons.jsonl, applying
// current-day decay inline. Returns the parsed lessons (score-desc,
// bounded by Limit) plus the count of rows skipped by the per-row
// guard. An unreadable store returns err — the caller decides whether
// to degrade (recall does) — but it is NOT an empty ledger; a missing
// file is the normal fresh state and returns (nil, 0, nil).
func (s *Store) LoadTieredLessons(tier string, o LoadOptions) ([]TieredLesson, int, error) {
	raw, err := os.ReadFile(s.TieredLessonsPath(tier))
	if os.IsNotExist(err) {
		return nil, 0, nil
	}
	if err != nil {
		return nil, 0, err
	}
	var results []TieredLesson
	skipped := 0
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		tl, ok := parseTieredLesson(line)
		if !ok {
			skipped++
			continue
		}
		days := DaysSince(tl.LastReinforced)
		if o.MaxAgeDays > 0 && days > o.MaxAgeDays {
			continue // lesson too stale — filtered, not skipped-as-broken
		}
		if !o.Raw && tier == TierMedium && days > 0 {
			tl.Score = DecayScore(tl.Score, days)
		}
		if !o.Raw && tl.Score < o.MinScore {
			continue
		}
		if o.TaskType != "" && tl.TaskType != o.TaskType {
			continue
		}
		if o.LessonType != "" && tl.LessonType != o.LessonType {
			continue
		}
		results = append(results, tl)
	}
	sort.SliceStable(results, func(i, j int) bool {
		return results[i].Score > results[j].Score
	})
	if o.Limit > 0 && len(results) > o.Limit {
		results = results[:o.Limit]
	}
	return results, skipped, nil
}

// parseTieredLesson decodes one JSONL row with Python's construction
// semantics: unknown keys ignored, missing keys default, numeric fields
// coerced (int/float/numeric-string), truthiness for the boolean-ish
// fields. Any field that cannot coerce fails the ROW (ok=false), never
// the load.
func parseTieredLesson(line string) (TieredLesson, bool) {
	// Byte-tainted rows fail the ROW, matching jsonl_utils.loads_clean's
	// posture. Go's encoding/json would otherwise absorb invalid UTF-8 as
	// U+FFFD replacement runes — laundering the corruption signal the
	// Python loader exists to preserve (a torn line must read as torn).
	if !utf8.ValidString(line) {
		return TieredLesson{}, false
	}
	dec := json.NewDecoder(strings.NewReader(line))
	dec.UseNumber()
	var m map[string]any
	if dec.Decode(&m) != nil || m == nil {
		return TieredLesson{}, false
	}
	var tl TieredLesson
	ok := true
	str := func(key string, dst *string) {
		v, present := m[key]
		if !present || v == nil {
			return
		}
		s, isStr := v.(string)
		if !isStr {
			ok = false // Go-stricter: type-drifted string field skips the row
			return
		}
		*dst = s
	}
	str("lesson_id", &tl.LessonID)
	str("task_type", &tl.TaskType)
	str("outcome", &tl.Outcome)
	str("lesson", &tl.Lesson)
	str("source_goal", &tl.SourceGoal)
	str("tier", &tl.Tier)
	str("last_reinforced", &tl.LastReinforced)
	str("recorded_at", &tl.RecordedAt)
	str("lesson_type", &tl.LessonType)
	str("minted_from", &tl.MintedFrom)
	str("minted_by", &tl.MintedBy)
	str("scope", &tl.Scope)
	if v, present := m["acquired_for"]; present && v != nil {
		if s, isStr := v.(string); isStr {
			tl.AcquiredFor = &s
		} else {
			ok = false
		}
	}
	fl := func(key string, dst *float64) {
		if v, present := m[key]; present && v != nil {
			f, cok := coerceFloat(v)
			if !cok {
				ok = false
				return
			}
			*dst = f
		}
	}
	fl("confidence", &tl.Confidence)
	fl("score", &tl.Score)
	fl("novelty", &tl.Novelty)
	in := func(key string, dst *int) {
		if v, present := m[key]; present && v != nil {
			n, cok := coerceInt(v)
			if !cok {
				ok = false
				return
			}
			*dst = n
		}
	}
	in("sessions_validated", &tl.SessionsValidated)
	in("times_applied", &tl.TimesApplied)
	in("times_reinforced", &tl.TimesReinforced)
	// Truthiness fields — Python bool(x): "false" is truthy, {} is not.
	tl.Provisional = Truthy(m["provisional"])
	if Truthy(m["contested"]) {
		// IsContested keys on map emptiness; a non-map truthy value
		// (schema drift) must still read as contested, so it lands as a
		// carrier entry rather than being absorbed.
		if cm, isMap := m["contested"].(map[string]any); isMap {
			tl.Contested = cm
		} else {
			tl.Contested = map[string]any{"value": m["contested"]}
		}
	}
	if im, isMap := m["imported"].(map[string]any); isMap {
		tl.Imported = im
	}
	if dm, isMap := m["delta_evidence"].(map[string]any); isMap {
		tl.DeltaEvidence = dm
	}
	if cm, isMap := m["canon"].(map[string]any); isMap {
		tl.Canon = cm
	}
	tl.EvidenceSources = CoerceEvidenceSources(m["evidence_sources"])
	tl.MergedVariants = stringList(m["merged_variants"])
	if gr, isList := m["grounding"].([]any); isList {
		for _, g := range gr {
			if gm, isMap := g.(map[string]any); isMap {
				tl.Grounding = append(tl.Grounding, gm)
			}
		}
	}
	return tl, ok
}

// CoerceEvidenceSources is the ONE owner of evidence_sources coercion.
// The field feeds the ranker's citation-penalty check, so coercion must
// preserve Python TRUTHINESS, not just shape: Python's duck-typed row
// carries a drifted non-list value as-is and bool(evidence_sources)
// still reads a non-empty string as CITED. A shape-only assertion
// silently flipped exactly that row to "uncited" (adversarial recall
// r1 2026-08-22, Skeptic + Expert QA independently) — and the pack
// IMPORT lane had the same assertion as a WRITER, durably baking [] into
// the store for a drifted incoming row (r2, both lenses again): the
// class census of the r1 fix missed its writer sibling. A truthy
// non-list lands as a one-element carrier; citedness survives and the
// drift stays visible. Falsy (absent/null/empty) returns nil.
func CoerceEvidenceSources(v any) []any {
	if ev, isList := v.([]any); isList {
		return ev
	}
	if Truthy(v) {
		return []any{v}
	}
	return nil
}

// coerceFloat matches Python float() — numbers and numeric strings
// pass, bools count as 1/0, anything else fails the row — with one
// deliberate Go-stricter refusal: non-finite results (NaN/±Inf, which
// strconv.ParseFloat happily produces from the strings "NaN" and
// "Infinity", and Python's float() equally accepts) fail the row. A
// NaN score is poison downstream — `NaN < MinScore` is always false,
// so the row would survive EVERY score filter uncounted (adversarial
// recall r1 2026-08-22, Skeptic). No writer in either runtime emits
// non-finite scores; refusing them is the safe direction.
// The decoder runs UseNumber, so no float64 arm is needed from the
// only caller (adversarial r1, Minimalist — the dead arms are gone).
func coerceFloat(v any) (float64, bool) {
	switch t := v.(type) {
	case json.Number:
		f, err := t.Float64()
		return f, err == nil && !math.IsNaN(f) && !math.IsInf(f, 0)
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(t), 64)
		return f, err == nil && !math.IsNaN(f) && !math.IsInf(f, 0)
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	}
	return 0, false
}

// coerceInt matches Python int(): integers and integer strings pass,
// floats truncate toward zero, a fractional STRING ("3.7") fails —
// int("3.7") raises in Python and the row skips there too.
func coerceInt(v any) (int, bool) {
	switch t := v.(type) {
	case json.Number:
		if n, err := t.Int64(); err == nil {
			return int(n), true
		}
		f, err := t.Float64()
		return int(f), err == nil
	case string:
		n, err := strconv.ParseInt(strings.TrimSpace(t), 10, 64)
		return int(n), err == nil
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	}
	return 0, false
}

// truthy ports Python bool(): nil/false/0/""/empty containers are
// false; everything else — including the string "false" — is true.
// Truthy is Python bool() semantics over decoded-JSON values. Exported
// because it is LOAD-BEARING at the pack-import boundary too: every
// gate-feeding boolean crossing that boundary (provisional) must
// preserve Python truthiness, not JSON shape — the same rule
// CoerceEvidenceSources owns for the container case (adversarial
// recall r3 2026-08-22: the r2 fix generalized the mechanism for one
// field but not the rule across the function it was editing).
func Truthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	case json.Number:
		f, err := t.Float64()
		return err != nil || f != 0
	case []any:
		return len(t) > 0
	case map[string]any:
		return len(t) > 0
	}
	return true
}

// ScoredLesson pairs a lesson with the exact number its ranking sorted
// on (query_lessons_scored's tuple).
type ScoredLesson struct {
	Lesson TieredLesson
	Score  float64
}

// QueryLessonsScored ports knowledge_web.query_lessons_scored on the
// TF-IDF lane: pool LONG + MEDIUM over the FULL live store (limit=None —
// relevance filtering is the ranker's job; the store stays bounded by
// decay + GC, not by hiding rows from retrieval), drop provisional /
// quarantined / contested, rank, return the top n. The hybrid BM25/RRF
// ranker behind Python's _USE_HYBRID is deliberately unported (optional
// dependency; TF-IDF is Python's own always-available fallback).
// Unreadable tiers degrade to their absence — recall's direction — with
// the failure reported through skippedOut rather than swallowed silently
// (skippedOut sums per-row skips; loadErrs names unreadable tiers).
func (s *Store) QueryLessonsScored(query string, n int, taskType string) (ranked []ScoredLesson, skippedOut int, loadErrs []string) {
	var candidates []TieredLesson
	for _, tier := range []string{TierLong, TierMedium} {
		pool, skipped, err := s.LoadTieredLessons(tier, LoadOptions{
			TaskType: taskType, // zero Limit = full store (limit=None)
		})
		skippedOut += skipped
		if err != nil {
			loadErrs = append(loadErrs, tier+": "+err.Error())
			continue
		}
		for _, tl := range pool {
			if tl.Provisional || IsQuarantined(tl) || IsContested(tl) {
				continue
			}
			candidates = append(candidates, tl)
		}
	}
	if len(candidates) == 0 {
		return nil, skippedOut, loadErrs
	}
	ranked = TFIDFRankScored(query, candidates, n)
	// NOT redundant with the ranker's own topK: the no-signal path
	// returns ALL lessons ignoring topK (the deliberate parity quirk),
	// and this caller-side bound is Python's ranked[:n] — exactly the
	// slice that tames it there too.
	if len(ranked) > n {
		ranked = ranked[:n]
	}
	return ranked, skippedOut, loadErrs
}
