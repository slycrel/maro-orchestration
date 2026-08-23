package skills

import (
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// SkillStats mirrors skill_types.SkillStats. Field order matches
// to_dict()'s key order.
//
// The legacy counters (total_uses/successes) credit keyword-matched
// bystanders with step completions at a ~1.0 base rate — inflated. The
// injected_* counters are the honest ones: counted at closure-verdict time
// for skills that were ACTUALLY in the run's injected-prompt manifest, with
// the run's FULL-trust goal verdict as the label. Consumers should prefer
// them where present.
type SkillStats struct {
	SkillID               string  `json:"skill_id"`
	SkillName             string  `json:"skill_name"`
	TotalUses             int     `json:"total_uses"`
	Successes             int     `json:"successes"`
	Failures              int     `json:"failures"`
	LastUsed              string  `json:"last_used"`
	SuccessRate           float64 `json:"success_rate"`
	NeedsEscalation       bool    `json:"needs_escalation"`
	TotalCostUSD          float64 `json:"total_cost_usd"`
	AvgLatencyMS          float64 `json:"avg_latency_ms"`
	AvgConfidence         float64 `json:"avg_confidence"`
	InjectedRuns          int     `json:"injected_runs"`
	InjectedSuccesses     int     `json:"injected_successes"`
	InjectedSuccessRate   float64 `json:"injected_success_rate"`
	LastInjectedVerdictAt string  `json:"last_injected_verdict_at"`
}

func newStats(skillID, skillName string) SkillStats {
	return SkillStats{SkillID: skillID, SkillName: skillName,
		SuccessRate: 1.0, AvgConfidence: 1.0}
}

// statsFromRow is SkillStats.from_dict: a COERCING constructor. Callers
// that decide what to keep must run validateStatsRow first.
func statsFromRow(d map[string]any) SkillStats {
	s := newStats("", "")
	getStr(d, "skill_id", &s.SkillID)
	getStr(d, "skill_name", &s.SkillName)
	getInt(d, "total_uses", &s.TotalUses)
	getInt(d, "successes", &s.Successes)
	getInt(d, "failures", &s.Failures)
	getStr(d, "last_used", &s.LastUsed)
	getFloat(d, "success_rate", &s.SuccessRate)
	if v, ok := d["needs_escalation"]; ok {
		s.NeedsEscalation = pyBool(v)
	}
	getFloat(d, "total_cost_usd", &s.TotalCostUSD)
	getFloat(d, "avg_latency_ms", &s.AvgLatencyMS)
	getFloat(d, "avg_confidence", &s.AvgConfidence)
	getInt(d, "injected_runs", &s.InjectedRuns)
	getInt(d, "injected_successes", &s.InjectedSuccesses)
	getFloat(d, "injected_success_rate", &s.InjectedSuccessRate)
	getStr(d, "last_injected_verdict_at", &s.LastInjectedVerdictAt)
	return s
}

// toRow is SkillStats.to_dict — the exact key set Python emits.
func (s SkillStats) toRow() map[string]any {
	return map[string]any{
		"skill_id": s.SkillID, "skill_name": s.SkillName,
		"total_uses": s.TotalUses, "successes": s.Successes,
		"failures": s.Failures, "last_used": s.LastUsed,
		"success_rate": s.SuccessRate, "needs_escalation": s.NeedsEscalation,
		"total_cost_usd": s.TotalCostUSD, "avg_latency_ms": s.AvgLatencyMS,
		"avg_confidence": s.AvgConfidence, "injected_runs": s.InjectedRuns,
		"injected_successes":       s.InjectedSuccesses,
		"injected_success_rate":    s.InjectedSuccessRate,
		"last_injected_verdict_at": s.LastInjectedVerdictAt,
	}
}

// EfficiencyScore is the cost-adjusted success rate — higher is better.
// Zero below three uses (not enough data).
func (s SkillStats) EfficiencyScore() float64 {
	if s.TotalUses < 3 {
		return 0
	}
	uses := s.TotalUses
	if uses < 1 {
		uses = 1
	}
	costPerRun := s.TotalCostUSD / float64(uses)
	penalty := math.Min(0.5, costPerRun*100)
	return math.Max(0, s.SuccessRate-penalty)
}

func skillStatsPath(ws string) string {
	return filepath.Join(ws, "memory", "skill-stats.jsonl")
}

// validateStatsRow proves a stats row is one the coercing constructor
// cannot distort. Checks the RAW values; absence is fine (stats rows are
// sparse upserts), presence must be the type the model would write.
//
// Presence is "key exists", NOT "value is non-null": an explicitly stored
// JSON null slipped through the absence exemption in Python, was laundered
// to false on the next counter bump, and a null counter field would make
// the NEXT update fail mid-recorder. No modeled field is nullable.
//
// Deliberately TYPE-level, not plausibility-level: a row claiming
// total_uses=-4, successes=100 is faithfully representable and faithfully
// re-emitted, so it is ADMITTED — semantic auditing is an inspector's job,
// and stranding implausible-but-readable rows would misfile legitimate
// legacy data behind a corruption warning.
//
// Identity is part of the predicate: the reader keys this store on a
// NON-EMPTY STRING skill_id, so a writer must never mint a row the reader
// will strand as keyless.
func validateStatsRow(d map[string]any) error {
	sid, ok := d["skill_id"].(string)
	if !ok || sid == "" {
		return fmt.Errorf("skill_id must be a non-empty string, got %#v", d["skill_id"])
	}
	for _, name := range []string{"total_uses", "successes", "failures",
		"injected_runs", "injected_successes"} {
		if v, present := d[name]; present && !isIntValue(v) {
			return fmt.Errorf("%s must be an int, got %#v", name, v)
		}
	}
	for _, name := range []string{"success_rate", "total_cost_usd",
		"avg_latency_ms", "avg_confidence", "injected_success_rate"} {
		if v, present := d[name]; present {
			f, isNum := numberOf(v)
			if !isNum || math.IsNaN(f) || math.IsInf(f, 0) {
				return fmt.Errorf("%s must be a finite number, got %#v", name, v)
			}
		}
	}
	for _, name := range []string{"skill_name", "last_used",
		"last_injected_verdict_at"} {
		if v, present := d[name]; present {
			if _, isStr := v.(string); !isStr {
				return fmt.Errorf("%s must be a string, got %#v", name, v)
			}
		}
	}
	if v, present := d["needs_escalation"]; present {
		if _, isBool := v.(bool); !isBool {
			return fmt.Errorf("needs_escalation must be a bool, got %#v", v)
		}
	}
	return nil
}

// statsRead is the announced read's result: the keyed map, plus every line
// the rebuild cannot represent (so a write-back re-emits them VERBATIM
// instead of deleting them), plus the count of older same-id duplicates the
// keyed read excluded.
type statsRead struct {
	records map[string]map[string]any
	order   []string // first-seen id order, so a rewrite is deterministic
	// strandedIDs are ids whose row is PRESENT but unprovable. Recovering
	// them is what lets a write refuse to mint over evidence it cannot read
	// — see statsFor.
	strandedIDs map[string]bool
	stranded    []string
	compacted   int
	tainted     int
	keyless     int
}

// readSkillStats is the announced read of skill-stats.jsonl.
//
// Before Python's 2026-08-17 fix, the read was a strict whole-file decode
// swallowed by a bare except, so ONE crash-torn byte left the map empty and
// the very next write rebuilt the store from it — every skill's stats
// destroyed by a hot-path counter update (probed live: 4 lines → 1).
//
// An unreadable store RAISES rather than returning empty: refusing to
// rebuild from nothing leaves the file intact, which is the safe direction
// when the alternative is a wipe. Pure readers degrade to empty themselves.
func readSkillStats(path string) (statsRead, error) {
	res := statsRead{records: map[string]map[string]any{},
		strandedIDs: map[string]bool{}}
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return res, nil
		}
		return res, err
	}
	// Split the RAW text, never a trimmed copy: this feeds a REWRITE, and
	// a trim can break a valid row at a Unicode separator inside a JSON
	// string or delete a whitespace-only row outright, unstranded and
	// uncounted.
	for _, line := range strings.Split(string(raw), "\n") {
		if record.IsFrameBlank(line) {
			continue
		}
		d, err := record.LoadsClean(line)
		if err != nil {
			res.tainted++
			res.stranded = append(res.stranded, line)
			continue
		}
		sid, isStr := d["skill_id"].(string)
		if isStr && sid != "" {
			// Admitted == provable: this map feeds a COERCING constructor,
			// so a schema-drifted row would be silently rewritten with
			// laundered values by the next routine counter bump.
			if err := validateStatsRow(d); err != nil {
				res.tainted++
				res.stranded = append(res.stranded, line)
				// The row failed the proof, but its id is readable. Hold it:
				// without this, the next counter bump minted a fresh zeroed
				// record for the same id and — because strandees ride FIRST
				// — the reset row landed last and won the last-row-wins read
				// in BOTH runtimes. A row this reader cannot prove is not a
				// row whose evidence may be replaced.
				res.strandedIDs[sid] = true
				continue
			}
		} else {
			// A non-empty STRING, not merely truthy. JSON 1 and JSON true
			// are distinct stored rows that Python keys equal (1 == True),
			// so the second silently overwrote the first and the rewrite
			// deleted a row with no strand and no warning. A key this store
			// cannot represent faithfully strands like any other unreadable
			// row. (Go would not conflate them, but the row is still
			// keyless for this store, and stranding keeps both runtimes'
			// files identical.)
			res.keyless++
			res.stranded = append(res.stranded, line)
			continue
		}
		if _, seen := res.records[sid]; seen {
			// Same id twice is representable (last wins, matching this
			// keyed read) but it is still N rows becoming one on the next
			// rewrite — count it instead of compacting in silence.
			res.compacted++
		} else {
			res.order = append(res.order, sid)
		}
		res.records[sid] = d
	}
	return res, nil
}

// writeSkillStats is the crash-safe, byte-safe write-back of the keyed
// store plus its strandees.
//
// Three properties Python's arc paid for:
//   - Every row is PROVEN before anything is written (the reader's full
//     predicate, not just clean-object JSON), so the store can never hold a
//     row the writer vouched for and no reader will return. The payload is
//     built before the write runs, so a refusal aborts with the store
//     intact.
//   - The map key and the row's own skill_id must AGREE, or a rekeyed entry
//     would silently write a row the next keyed read files under a
//     different id than the caller updated.
//   - Strandees ride FIRST. With them at the tail, a same-id stranded row
//     overrode the repaired one for any naive last-row-wins parser.
func writeSkillStats(path string, r statsRead) error {
	var lines []string
	for _, key := range r.order {
		d := r.records[key]
		if sid, _ := d["skill_id"].(string); sid != key {
			return fmt.Errorf("records key %q disagrees with row skill_id %q", key, sid)
		}
		if err := validateStatsRow(d); err != nil {
			return err
		}
		line, err := proveStatsLine(d)
		if err != nil {
			return err
		}
		lines = append(lines, line)
	}
	var sb strings.Builder
	for _, s := range r.stranded {
		sb.WriteString(s)
		sb.WriteByte('\n')
	}
	for _, l := range lines {
		sb.WriteString(l)
		sb.WriteByte('\n')
	}
	return record.AtomicWrite(path, []byte(sb.String()))
}

// writeAnnouncement is what the WRITE did, said only after its commit: the
// carry-through of strandees, and the deletion the keyed read predicted,
// announced by the rewrite that actually performed it.
func writeAnnouncement(path string, r statsRead) []string {
	var out []string
	if len(r.stranded) > 0 {
		out = append(out, fmt.Sprintf("skill-stats: %d stranded row(s) "+
			"carried through the rewrite verbatim (%s)", len(r.stranded), path))
	}
	if r.compacted > 0 {
		out = append(out, fmt.Sprintf("skill-stats: %d older duplicate row(s) "+
			"compacted by this rewrite — last row per id won (%s)",
			r.compacted, path))
	}
	return out
}

// proveStatsLine serializes one stats row AND proves the strict reader
// admits it: non-finite telemetry would otherwise write a token the reader
// strands, and a surrogate-bearing skill_id would serialize as a clean
// escape — the recorder reporting an outcome no reader can ever return.
func proveStatsLine(d map[string]any) (string, error) {
	raw, err := marshalStatsRow(d)
	if err != nil {
		return "", err
	}
	back, err := record.LoadsClean(raw)
	if err != nil {
		return "", fmt.Errorf("emitted stats line would not be admitted: %w", err)
	}
	if err := validateStatsRow(back); err != nil {
		return "", fmt.Errorf("emitted stats line would not validate: %w", err)
	}
	return raw, nil
}

// statsKeyOrder is SkillStats.to_dict()'s key order.
var statsKeyOrder = []string{"skill_id", "skill_name", "total_uses",
	"successes", "failures", "last_used", "success_rate", "needs_escalation",
	"total_cost_usd", "avg_latency_ms", "avg_confidence",
	"injected_runs", "injected_successes", "injected_success_rate",
	"last_injected_verdict_at"}

func marshalStatsRow(d map[string]any) (string, error) {
	return marshalOrdered(d, statsKeyOrder)
}

// marshalOrdered and marshalValue are pyjson's, kept as local names so the
// emitters in this package read as one family. The implementation is shared
// because the three ways encoding/json differs from json.dumps (sorted keys,
// HTML escaping, bare whole floats) kept reappearing one package at a time.
func marshalOrdered(d map[string]any, modeled []string) (string, error) {
	return pyjson.Ordered(d, modeled)
}

func marshalValue(v any) (string, error) { return pyjson.Value(v) }

// StatsLoad is what a read of the stats store returned AND what it had to
// leave out — mirroring LoadResult for the skill library. The excluded
// counts are part of the result, not a log line, because every caller that
// acts on this data is entitled to know the data is partial.
type StatsLoad struct {
	Stats     []SkillStats
	Tainted   int // unparseable or unprovable rows, stranded verbatim
	Keyless   int // no representable skill_id, stranded verbatim
	Compacted int // older same-id rows this keyed read excluded
	Err       error
}

// Warnings renders the read's announcement. A READ announces what the read
// did — exclusion from this result — never a rewrite it has no knowledge
// of: pure readers used to log "carried through the rewrite" with no
// rewrite anywhere. The carry-through claim belongs after a commit.
func (l StatsLoad) Warnings(path string) []string {
	var out []string
	if l.Tainted > 0 || l.Keyless > 0 {
		out = append(out, fmt.Sprintf("skill-stats: %d unparseable/unprovable "+
			"and %d keyless row(s) excluded from this read; they remain in "+
			"the store verbatim (%s)", l.Tainted, l.Keyless, path))
	}
	if l.Compacted > 0 {
		out = append(out, fmt.Sprintf("skill-stats: %d older duplicate row(s) "+
			"excluded from this read (%s)", l.Compacted, path))
	}
	return out
}

// LoadSkillStats reads every stats record. A pure reader: an unreadable
// store degrades to empty (with Err set) rather than raising.
func LoadSkillStats(ws string) StatsLoad {
	r, err := readSkillStats(skillStatsPath(ws))
	if err != nil {
		return StatsLoad{Err: err}
	}
	out := make([]SkillStats, 0, len(r.order))
	for _, id := range r.order {
		out = append(out, statsFromRow(r.records[id]))
	}
	return StatsLoad{Stats: out, Tainted: r.tainted,
		Keyless: r.keyless, Compacted: r.compacted}
}

// GetAllSkillStats is the convenience read for callers that only want the
// records.
func GetAllSkillStats(ws string) []SkillStats { return LoadSkillStats(ws).Stats }

// GetSkillStats returns one skill's stats, or false when absent.
func GetSkillStats(ws, skillID string) (SkillStats, bool) {
	r, err := readSkillStats(skillStatsPath(ws))
	if err != nil {
		return SkillStats{}, false
	}
	row, ok := r.records[skillID]
	if !ok {
		return SkillStats{}, false
	}
	return statsFromRow(row), true
}

// OutcomeTelemetry carries the optional per-invocation measurements.
type OutcomeTelemetry struct {
	CostUSD    float64
	LatencyMS  float64
	Confidence float64 // 1.0 means "no data"
}

// RecordSkillOutcome upserts one skill invocation outcome.
//
// Evidence must arrive as evidence: the id must be non-empty encodable
// text (a non-string id minted the exact row the reader strands as
// keyless) and the telemetry must be finite. Both are refused AT THE DOOR,
// before any lock or mutation.
//
// The whole transaction runs under the store lock with require semantics: a
// read-modify-write over the whole keyed store degraded by a fail-open lock
// is the classic lost update — two recorders both read N, both write N+1,
// one outcome silently gone. A transaction that cannot lock must refuse to
// run.
//
// An error is RETURNED, never swallowed: an earlier Python version warned
// and returned normally, so a disk-full lost the outcome while the caller
// proceeded as if evidence existed. An error result must not be a valid
// value.
func RecordSkillOutcome(ws, skillID string, success bool, tel OutcomeTelemetry) ([]string, error) {
	if skillID == "" {
		return nil, fmt.Errorf("skill_id must be a non-empty string")
	}
	if !isCleanText(skillID) {
		return nil, fmt.Errorf("skill_id is not encodable text: %q", skillID)
	}
	// A SLICE, not a map: Python checks these three in a fixed tuple order,
	// so when a caller passes more than one non-finite value it always
	// names cost_usd first. Ranging a map named a different field on
	// different runs of the same input — 3 distinct messages over 200
	// identical calls (adversarial r4, L3) — and this message is returned
	// to the caller's warning rail, which is durable. `%v` also spelled
	// the values `NaN`/`+Inf` where Python's `!r` spells `nan`/`inf`.
	for _, f := range []struct {
		name string
		v    float64
	}{{"cost_usd", tel.CostUSD}, {"latency_ms", tel.LatencyMS},
		{"confidence", tel.Confidence}} {
		if math.IsNaN(f.v) || math.IsInf(f.v, 0) {
			return nil, fmt.Errorf("%s must be a finite number, got %s",
				f.name, pyjson.FloatRepr(f.v))
		}
	}
	path := skillStatsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), record.NewDirMode); err != nil {
		return nil, err
	}
	var warns []string
	err := record.Locked(path, func() error {
		r, err := readSkillStats(path)
		if err != nil {
			return err
		}
		stats, existed, err := statsFor(ws, r, skillID)
		if err != nil {
			return err
		}

		prevUses := stats.TotalUses
		stats.TotalUses++
		if success {
			stats.Successes++
		} else {
			stats.Failures++
		}
		stats.LastUsed = nowISO()
		uses := stats.TotalUses
		if uses < 1 {
			uses = 1
		}
		stats.SuccessRate = float64(stats.Successes) / float64(uses)
		stats.NeedsEscalation = stats.SuccessRate < EscalationThreshold

		if tel.CostUSD != 0 {
			stats.TotalCostUSD += tel.CostUSD
		}
		n := float64(stats.TotalUses)
		if tel.LatencyMS != 0 {
			stats.AvgLatencyMS = stats.AvgLatencyMS*(float64(prevUses)/n) + tel.LatencyMS/n
		}
		if tel.Confidence != 1.0 {
			stats.AvgConfidence = stats.AvgConfidence*(float64(prevUses)/n) + tel.Confidence/n
		}
		mergeStats(&r, skillID, stats, existed)
		if err := writeSkillStats(path, r); err != nil {
			return err
		}
		warns = append(warns, writeAnnouncement(path, r)...)
		return nil
	})
	return warns, err
}

// RecordSkillInjectionOutcomes applies a run's verdict to every skill in
// its injected manifest, as ONE transaction.
//
// The batch shape is the point: a per-id loop made a mid-list failure a
// reachable partial batch — id A committed, id B failed, the idempotence
// marker never written — so a retry double-counted A.
func RecordSkillInjectionOutcomes(ws string, skillIDs []string, goalAchieved bool) ([]string, error) {
	var ids []string
	seen := map[string]bool{}
	dup := 0
	for _, id := range skillIDs {
		// The shared id door, and it REFUSES rather than skipping: an empty
		// id in a manifest means the caller's manifest is wrong, and
		// recording the rest of the batch hides that while writing a
		// verdict set that does not match the run. Python's
		// _require_recordable_id raises here and nothing is recorded.
		if id == "" {
			return nil, fmt.Errorf("skill_id must be a non-empty string, got ''")
		}
		if !isCleanText(id) {
			return nil, fmt.Errorf("skill_id is not encodable text: %q", id)
		}
		// One verdict per skill per batch: a duplicated id would credit one
		// injected run twice. First-seen order is kept; the collapse is
		// ANNOUNCED, because a silently smaller denominator is exactly the
		// kind of quiet arithmetic that makes A/B evidence untrustworthy.
		if seen[id] {
			dup++
			continue
		}
		seen[id] = true
		ids = append(ids, id)
	}
	if len(ids) == 0 {
		return nil, nil
	}
	var doorWarns []string
	if dup > 0 {
		doorWarns = append(doorWarns, fmt.Sprintf("skill-stats: %d duplicate "+
			"id(s) collapsed — one verdict per skill per batch", dup))
	}
	path := skillStatsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), record.NewDirMode); err != nil {
		return nil, err
	}
	warns := doorWarns
	err := record.Locked(path, func() error {
		r, err := readSkillStats(path)
		if err != nil {
			return err
		}
		now := nowISO()
		for _, id := range ids {
			// A batch is ONE transaction: a refusal aborts the whole batch
			// with the store untouched, rather than recording some verdicts
			// and leaving a retry to double-count the rest.
			stats, existed, err := statsFor(ws, r, id)
			if err != nil {
				return err
			}
			stats.InjectedRuns++
			if goalAchieved {
				stats.InjectedSuccesses++
			}
			runs := stats.InjectedRuns
			if runs < 1 {
				runs = 1
			}
			stats.InjectedSuccessRate = float64(stats.InjectedSuccesses) / float64(runs)
			stats.LastInjectedVerdictAt = now
			mergeStats(&r, id, stats, existed)
		}
		if err := writeSkillStats(path, r); err != nil {
			return err
		}
		warns = append(warns, writeAnnouncement(path, r)...)
		return nil
	})
	return warns, err
}

// statsFor finds or creates the record for one id, naming it from the skill
// library when the row is new.
// statsFor returns the stored record for an id, or a fresh one to mint.
//
// It REFUSES when the store holds an unprovable row for that id: minting
// there is not a create, it is a silent overwrite of evidence, and the
// reset row wins the keyed read. The honest answer is to say the store
// needs repair.
func statsFor(ws string, r statsRead, skillID string) (SkillStats, bool, error) {
	if row, ok := r.records[skillID]; ok {
		return statsFromRow(row), true, nil
	}
	if r.strandedIDs[skillID] {
		return SkillStats{}, false, fmt.Errorf("skill-stats: %s has a live row "+
			"that cannot be proven — refusing to mint a fresh record over it, "+
			"which would reset its evidence; repair the row, then retry",
			pyRepr(skillID))
	}
	name := skillID
	for _, sk := range LoadSkills(ws).Skills {
		if sk.ID == skillID {
			name = sk.Name
			break
		}
	}
	return newStats(skillID, name), false, nil
}

// mergeStats writes the updated stats OVER the stored row rather than
// replacing it: toRow emits only the modeled schema, so any field this
// updater does not own — an operator's hand-added note, a forward-version
// field — would be deleted by every routine counter bump.
func mergeStats(r *statsRead, skillID string, stats SkillStats, existed bool) {
	merged := map[string]any{}
	if old, ok := r.records[skillID]; ok {
		for k, v := range old {
			merged[k] = v
		}
	}
	for k, v := range stats.toRow() {
		merged[k] = v
	}
	r.records[skillID] = merged
	if !existed {
		r.order = append(r.order, skillID)
	}
}

// nowISO matches the port-wide stamp (record/scans/graduation): fixed
// six-digit fractional seconds, so stored timestamps sort lexicographically
// the same way they compare as instants. Go's ".999999" would TRIM trailing
// zeros, making ".5+00:00" and ".500000+00:00" — the same instant — sort
// differently from Python's, which always writes six digits.
func nowISO() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05.000000-07:00")
}
