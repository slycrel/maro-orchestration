// Package knowledge is the minimal Python-schema-compatible surface the
// pack importer needs: append hypotheses (knowledge_lens.Hypothesis) and
// medium-tier lessons (knowledge_web.TieredLesson) as rows the Python
// readers load unchanged, plus the dedup-snapshot loaders those imports
// gate on.
//
// This is NOT the recall/knowledge tranche — no decay, no promotion, no
// retrieval. It exists so an import lands data where BOTH runtimes read
// it; the Python field sets are the contract (asdict() emits every field,
// so we emit every field too — a Go-written row and a Python-written row
// must be indistinguishable to a reader).
package knowledge

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Vocabulary constants shared with knowledge_web.py.
var LessonTypes = map[string]bool{
	"execution": true, "planning": true, "recovery": true,
	"verification": true, "cost": true,
}
var LessonScopes = map[string]bool{"method": true, "world": true}

// Variant-union bounds shared with memory_ledger.py.
const (
	MergedVariantsCap = 5
	VariantMaxChars   = 500
)

// CoerceScope ports knowledge_web.coerce_scope: any decoded-JSON value →
// (scope, bad). bad distinguishes "honestly unstamped" (absent/""/null)
// from "carries something no write path can produce" so corruption gets
// reported rather than absorbed. Type check before vocabulary check —
// scope can hold any decodable type.
func CoerceScope(raw any) (string, bool) {
	if s, ok := raw.(string); ok && LessonScopes[s] {
		return s, false
	}
	if raw == nil || raw == "" {
		return "", false
	}
	return "", true
}

// Hypothesis mirrors knowledge_lens.Hypothesis.to_dict exactly.
type Hypothesis struct {
	HypID           string   `json:"hyp_id"`
	Lesson          string   `json:"lesson"`
	Domain          string   `json:"domain"`
	Confirmations   int      `json:"confirmations"`
	Contradictions  int      `json:"contradictions"`
	SourceLessonIDs []string `json:"source_lesson_ids"`
	FirstSeen       string   `json:"first_seen"`
	LastSeen        string   `json:"last_seen"`
	// ORDERED, not a map. Python builds this block as a dict literal and
	// json.dumps writes a dict in insertion order; a Go map has none, so
	// DumpsStruct sorted it and the two runtimes wrote different bytes for
	// the same provenance chain into a LIVE shared store. The rest of the
	// row already keeps Python's order because DumpsStruct walks the struct
	// in field order — this was the one nested block that did not.
	Imported pyval.Obj `json:"imported"`
}

// TieredLesson mirrors knowledge_web.TieredLesson via asdict() — every
// declared field ships, matching what the Python append writes. Field
// additions on the Python side deserialize-to-default there, but keep
// this in sync when porting later tranches.
type TieredLesson struct {
	LessonID          string           `json:"lesson_id"`
	TaskType          string           `json:"task_type"`
	Outcome           string           `json:"outcome"`
	Lesson            string           `json:"lesson"`
	SourceGoal        string           `json:"source_goal"`
	Confidence        float64          `json:"confidence"`
	Tier              string           `json:"tier"`
	Score             float64          `json:"score"`
	LastReinforced    string           `json:"last_reinforced"`
	SessionsValidated int              `json:"sessions_validated"`
	TimesApplied      int              `json:"times_applied"`
	TimesReinforced   int              `json:"times_reinforced"`
	RecordedAt        string           `json:"recorded_at"`
	AcquiredFor       *string          `json:"acquired_for"`
	EvidenceSources   []any            `json:"evidence_sources"`
	LessonType        string           `json:"lesson_type"`
	Imported          pyval.Obj        `json:"imported"`
	Novelty           float64          `json:"novelty"`
	Provisional       bool             `json:"provisional"`
	MintedFrom        string           `json:"minted_from"`
	MintedBy          string           `json:"minted_by"`
	Scope             string           `json:"scope"`
	Contested         map[string]any   `json:"contested"`
	MergedVariants    []string         `json:"merged_variants"`
	DeltaEvidence     map[string]any   `json:"delta_evidence"`
	Grounding         []map[string]any `json:"grounding"`
	Canon             map[string]any   `json:"canon"`
}

// Store reads and appends the knowledge files of one workspace.
type Store struct{ WorkspaceDir string }

func NewStore(ws string) *Store { return &Store{WorkspaceDir: ws} }

func (s *Store) memory() string { return filepath.Join(s.WorkspaceDir, "memory") }

func (s *Store) HypothesesPath() string {
	return filepath.Join(s.memory(), "hypotheses.jsonl")
}
func (s *Store) StandingRulesPath() string {
	return filepath.Join(s.memory(), "standing_rules.jsonl")
}
func (s *Store) TieredLessonsPath(tier string) string {
	return filepath.Join(s.memory(), tier, "lessons.jsonl")
}

// readRows loads every decodable JSON-object row of a JSONL file,
// tolerating a missing file and skipping undecodable lines — the same
// posture as the Python loaders (nothing validates JSONL on read).
func readRows(path string) ([]map[string]any, error) {
	f, err := os.Open(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var rows []map[string]any
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 8*1024*1024)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal(line, &m); err != nil {
			continue
		}
		rows = append(rows, m)
	}
	return rows, sc.Err()
}

// DedupSnapshot is the identity state an import checks against: ids and
// texts already present. Taken once, before any appends (fixpoint review
// 2026-08-11: duplicate rows inside one artifact must not both import —
// the caller adds to it as it writes).
type DedupSnapshot struct {
	IDs   map[string]bool
	Texts map[string]bool
}

// HypothesisSnapshot gathers existing hypothesis ids plus hypothesis AND
// standing-rule texts (a rule that already exists locally must not
// re-enter as a hypothesis — same union the Python importer takes).
func (s *Store) HypothesisSnapshot() (*DedupSnapshot, error) {
	snap := &DedupSnapshot{IDs: map[string]bool{}, Texts: map[string]bool{}}
	hyps, err := readRows(s.HypothesesPath())
	if err != nil {
		return nil, err
	}
	for _, h := range hyps {
		if id, ok := h["hyp_id"].(string); ok {
			snap.IDs[id] = true
		}
		if t, ok := h["lesson"].(string); ok && t != "" {
			snap.Texts[t] = true
		}
	}
	rules, err := readRows(s.StandingRulesPath())
	if err != nil {
		return nil, err
	}
	for _, r := range rules {
		if t, ok := r["rule"].(string); ok && t != "" {
			snap.Texts[t] = true
		}
	}
	return snap, nil
}

// LessonSnapshot gathers existing lesson ids and texts across MEDIUM and
// LONG tiers (an import never writes LONG, but an identical text there
// must still dedup).
func (s *Store) LessonSnapshot() (*DedupSnapshot, error) {
	snap := &DedupSnapshot{IDs: map[string]bool{}, Texts: map[string]bool{}}
	for _, tier := range []string{"medium", "long"} {
		rows, err := readRows(s.TieredLessonsPath(tier))
		if err != nil {
			return nil, err
		}
		for _, r := range rows {
			if id, ok := r["lesson_id"].(string); ok {
				snap.IDs[id] = true
			}
			if t, ok := r["lesson"].(string); ok && t != "" {
				snap.Texts[t] = true
			}
		}
	}
	return snap, nil
}

// AppendHypothesis writes one hypotheses.jsonl row. Python is
// `locked_append(path, json.dumps(asdict(h)))`, so the bytes are
// json.dumps' — pyval, not encoding/json (adversarial mission-r8 HIGH:
// r7 converted the eight writers it enumerated and an enumeration is not
// a class; a struct LOOKS safe because encoding/json emits declaration
// order, and order was never the fork).
func (s *Store) AppendHypothesis(h Hypothesis) error {
	line, err := pyval.DumpsStruct(h)
	if err != nil {
		return err
	}
	raw := []byte(line)
	if err := os.MkdirAll(s.memory(), record.NewDirMode); err != nil {
		return err
	}
	return record.AppendRawLine(s.HypothesesPath(), raw)
}

// AppendMediumLesson writes one tiered-lessons row —
// `locked_append(path, json.dumps(asdict(tl)))` over there.
//
// This is THE file the whole port exists to keep interoperable, and it
// had all three forks at once: `>` HTML-escaped in every "A -> B"
// lesson, an accented lesson written raw where json.dumps writes
// \uXXXX, and — the one only a struct has — Confidence, Score and
// Novelty are float64 and routinely WHOLE, so json.Marshal wrote
// `"confidence":1` where json.dumps writes `"confidence": 1.0`.
func (s *Store) AppendMediumLesson(tl TieredLesson) error {
	tl.Tier = "medium"
	line, err := pyval.DumpsStruct(tl)
	if err != nil {
		return err
	}
	raw := []byte(line)
	path := s.TieredLessonsPath("medium")
	if err := os.MkdirAll(filepath.Dir(path), record.NewDirMode); err != nil {
		return err
	}
	return record.AppendRawLine(path, raw)
}

// AbsorbVariant ports memory_ledger._absorb_variant line for line — the
// ONE owner of the variant-union rule: skip empties, the canonical text
// itself, already-present texts, and everything past the cap. BOTH text
// and canonical are stripped (Python strips both; adversarial round
// 2026-08-22 caught the one-sided version), identity is judged before
// clipping AND re-judged after (the clipped twin of the canonical or of
// an existing variant is still a twin). Python's str.strip() is
// unicode-aware, hence strings.TrimSpace, not a hand-rolled ASCII trim
// (a trailing \r survived the old one and defeated cross-runtime dedup).
func AbsorbVariant(variants []string, text, canonical string) []string {
	if len(variants) >= MergedVariantsCap {
		return variants
	}
	// pytext.Strip, not TrimSpace: `_absorb_variant` calls `.strip()`, and
	// the difference lands in merged_variants — a stored field on a shared
	// tiered-lessons row. A variant of only information separators is
	// falsy in Python and dropped; TrimSpace leaves it non-empty and the
	// port stored it. A variant ENDING in one is stored with the separator
	// here and without it there, so the same lesson carries two different
	// variant lists in the two runtimes and each keeps re-absorbing the
	// other's spelling as new.
	trimmed := pytext.Strip(text)
	canonical = pytext.Strip(canonical)
	if trimmed == "" || trimmed == canonical {
		return variants
	}
	for _, v := range variants {
		if v == trimmed {
			return variants
		}
	}
	clipped := budget.Clip(trimmed, VariantMaxChars)
	if clipped == canonical {
		return variants
	}
	for _, v := range variants {
		if v == clipped {
			return variants
		}
	}
	return append(variants, clipped)
}

// UnionVariantsIntoLesson rewrites the tiered-lessons file for each tier,
// absorbing variants into the row whose canonical text is byte-identical
// — the transport-order-dependence fix from the 2026-08-11 fixpoint
// (identity collision skips the ROW, not its rationale). Whole-file
// read-modify-write under the store lock, like Python's
// _mutate_tiered_lessons. A miss is a no-op.
func (s *Store) UnionVariantsIntoLesson(lessonText string, variants []string) error {
	for _, tier := range []string{"medium", "long"} {
		path := s.TieredLessonsPath(tier)
		if _, err := os.Stat(path); os.IsNotExist(err) {
			continue // a tier with no store cannot hold the twin; no lock dir to create
		}
		err := record.Locked(path, func() error {
			raw, err := os.ReadFile(path)
			if os.IsNotExist(err) {
				return nil
			}
			if err != nil {
				return err
			}
			lines := splitLines(raw)
			changed := false
			for i, line := range lines {
				// LoadsOrdered, not a map decode: this is a
				// read-modify-WRITE of whole store rows, so BOTH of the
				// things a map loses matter. It keeps json.Number, which
				// is why a plain Unmarshal was wrong here before (a
				// >2^53 numeric field would round through float64 on the
				// rewrite — r4 2026-08-22, QA); and it keeps KEY ORDER,
				// which a map had already thrown away by the time the
				// renderer saw it. Python's _mutate_tiered_lessons
				// rewrites a dataclass and emits its field order, so a
				// row this runtime touched came back alphabetised
				// (adversarial mission-r8).
				parsed, perr := pyval.LoadsOrdered(line)
				if perr != nil {
					continue
				}
				row, ok := parsed.(pyval.Obj)
				if !ok {
					continue
				}
				canonical, _ := row.Get("lesson")
				canonicalText, _ := canonical.(string)
				if canonicalText != lessonText {
					continue
				}
				existing := stringList(mustGetOr(row, "merged_variants"))
				merged := existing
				for _, v := range variants {
					merged = AbsorbVariant(merged, v, canonicalText)
				}
				if len(merged) == len(existing) {
					continue
				}
				row.Set("merged_variants", pyval.FromPlain(merged))
				out, err := pyval.DumpsCompactPy(row)
				if err != nil {
					return err
				}
				lines[i] = out
				changed = true
			}
			if !changed {
				return nil
			}
			return atomicRewrite(path, lines)
		})
		if err != nil {
			return fmt.Errorf("variant union (%s): %w", tier, err)
		}
	}
	return nil
}

func splitLines(raw []byte) []string {
	var lines []string
	start := 0
	for i, b := range raw {
		if b == '\n' {
			if i > start {
				lines = append(lines, string(raw[start:i]))
			}
			start = i + 1
		}
	}
	if start < len(raw) {
		lines = append(lines, string(raw[start:]))
	}
	return lines
}

func stringList(v any) []string {
	arr, ok := v.([]any)
	if !ok {
		return nil
	}
	var out []string
	for _, e := range arr {
		if s, ok := e.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

// atomicRewrite replaces path via temp-file + rename (crash-safe; the
// caller holds the flock, so no other locked writer interleaves).
//
// record.AtomicWrite, not a second temp-file dance of its own. The
// hand-rolled version this replaces created the temp with os.CreateTemp
// and renamed it into place WITHOUT a Chmod, and os.CreateTemp creates
// 0600 -- so every rewrite of a shared lessons store narrowed it to
// 0600, both for an existing 0664 ledger and for a file created fresh.
// That is the same defect CPython's file_lock.atomic_write carries a
// comment about having already fixed once ("data-r2-03: rewrites
// silently narrow existing ledgers"), and it matters more here than
// there: the two runtimes share one workspace store by design, so a Go
// rewrite can leave a ledger the Python runtime cannot read.
// record.AtomicWrite is that function's port and already has the rule.
// It also fsyncs, which this did not and CPython does.
func atomicRewrite(path string, lines []string) error {
	var b strings.Builder
	for _, ln := range lines {
		b.WriteString(ln)
		b.WriteString("\n")
	}
	return record.AtomicWrite(path, []byte(b.String()))
}

// mustGetOr is pyval.Obj's `.get(key)`: the value, or nil when absent —
// the distinction a dict makes and a two-value Go lookup obscures at a
// call site that only wants the value.
func mustGetOr(o pyval.Obj, key string) any {
	v, _ := o.Get(key)
	return v
}
