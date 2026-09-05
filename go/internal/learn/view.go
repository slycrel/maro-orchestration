package learn

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// LessonsView materializes memory/lessons.jsonl (wire contract B7, the
// flat store) from the learn ledger folded at the announced head. It is a
// WholeView: a row is an item's standing, not an event, so the file is the
// fold's end state rendered once. The mapping row lives in
// contracts/VIEWS.md; the shape is the Python `Lesson` dataclass EXACTLY
// so `memory_ledger.load_lessons` reads it unchanged.
//
// Only what a Python reader may inject appears: the current lesson revision
// of each item whose stage is selectable, plus quarantined items stamped
// `contested` (kept on disk, excluded from injection — the B7 vocabulary
// for "this text is not to be used"). Candidate and observed revisions are
// not in the store at all: the flat vocabulary has no non-injecting stamp
// that means "unproven" rather than "retired", and an unproven Go lesson
// must not become an injected Python one. Tombstones and policies are
// omitted: an archive and process data respectively.
type LessonsView struct {
	Store *thought.Store
}

// LessonsFile is the B7 file name under the workspace's memory root.
const LessonsFile = "lessons.jsonl"

func (LessonsView) Name() string { return LessonsFile }

// LessonHandle is the B7 `lesson_id` of an item: 8 hex of a domain-separated
// hash of the item id, stable across re-renders and workspaces.
func LessonHandle(item LearnedID) string {
	sum := sha256.Sum256([]byte("lesson/1|" + string(item)))
	return hex.EncodeToString(sum[:])[:8]
}

// lessonRow is the flat Lesson dataclass, field for field, in its order.
type lessonRow struct {
	LessonID        string            `json:"lesson_id"`
	TaskType        string            `json:"task_type"`
	Outcome         string            `json:"outcome"`
	Lesson          string            `json:"lesson"`
	SourceGoal      string            `json:"source_goal"`
	Confidence      float64           `json:"confidence"`
	TimesApplied    int               `json:"times_applied"`
	TimesReinforced int               `json:"times_reinforced"`
	RecordedAt      string            `json:"recorded_at"`
	MintedFrom      string            `json:"minted_from,omitempty"`
	Contested       map[string]string `json:"contested,omitempty"`
	Imported        map[string]string `json:"imported,omitempty"`
}

// confidenceOf is the B7 confidence of a stage: the Python readers rank by
// it, so it must order the way the ladder does.
var confidenceOf = map[Stage]float64{Provisional: 0.7, Effective: 0.8, Canon: 0.9, Contested: 0.5, Quarantined: 0.3}

// pyISO is Python's datetime.isoformat() for an aware UTC time.
func pyISO(t time.Time) string { return t.UTC().Format("2006-01-02T15:04:05.000000+00:00") }

func (v LessonsView) Render(j *journal.Journal, head uint64, w io.Writer) error {
	led, err := Fold(j.Production().PinAt(head))
	if err != nil {
		return err
	}
	ids := make([]string, 0, len(led.Items))
	for id := range led.Items {
		ids = append(ids, string(id))
	}
	sort.Strings(ids)
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	for _, id := range ids {
		it := led.Items[LearnedID(id)]
		cur := it.Current
		if cur == nil || cur.LearnedKind != Lesson {
			continue
		}
		stage := it.StageOf(cur.ID)
		if _, ok := confidenceOf[stage]; !ok {
			continue // candidate, observed, tombstone: not in the injectable store
		}
		text, err := v.Store.Get(cur.Text)
		if err != nil {
			return fmt.Errorf("lesson %s revision %s: %w", id, cur.ID, err)
		}
		row := lessonRow{LessonID: LessonHandle(it.ID), TaskType: cur.Family, Lesson: string(text),
			Confidence: confidenceOf[stage], TimesApplied: len(led.Exposures[cur.ID]), RecordedAt: pyISO(cur.At)}
		switch cur.Provenance.Source {
		case "tail":
			row.MintedFrom = "outcome"
		case SourceImport:
			row.Imported = map[string]string{"source": SourceImport, "why": cur.Provenance.Why}
		}
		if stage == Quarantined {
			at := cur.At
			if trs := it.Transitions[cur.ID]; len(trs) > 0 {
				at = trs[len(trs)-1].At
			}
			row.Contested = map[string]string{"reason": string(ItemHarmful), "source": "maro-go", "contested_at": pyISO(at)}
		}
		if err := enc.Encode(row); err != nil {
			return err
		}
	}
	return nil
}
