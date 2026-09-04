package learn

import (
	"fmt"
	"sort"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Item is one learned identity: its revisions in order, the current one,
// and the standing of EACH revision (evidence never moves another's).
type Item struct {
	ID          LearnedID
	Revisions   []*LearnedRevision
	Current     *LearnedRevision
	Stage       map[record.RecordID]Stage
	Transitions map[record.RecordID][]*LifecycleTransition
}

// StageOf is a revision's standing: candidate until a transition says otherwise.
func (it *Item) StageOf(rev record.RecordID) Stage {
	if s, ok := it.Stage[rev]; ok {
		return s
	}
	return Candidate
}

// Ledger is the fold of the learned population.
type Ledger struct {
	Items        map[LearnedID]*Item
	Applications map[record.RecordID][]*Application // by invocation, in Seq order
	Recalls      map[string]*RecallSelection        // by "<run>/<attempt>"
}

// RecallKey names an attempt's recall selection in the ledger.
func RecallKey(run record.RunID, attempt uint32) string { return fmt.Sprintf("%s/%d", run, attempt) }

// Fold folds the learned records and refuses what no writer could have
// produced: a revision whose predecessor is not the item's current one (or
// a first revision with one), a kind change across revisions, a transition
// for an unknown revision or from a stage the revision is not at, a
// transition whose evidence is not an earlier record, an application for
// an unknown revision, two recalls for one attempt.
func Fold(pr *journal.ProductionReader) (*Ledger, error) {
	led := &Ledger{Items: map[LearnedID]*Item{}, Applications: map[record.RecordID][]*Application{}, Recalls: map[string]*RecallSelection{}}
	seen := map[record.RecordID]bool{}
	err := pr.Scan(0, func(r record.Record) error {
		seen[r.Head().ID] = true
		switch x := r.(type) {
		case *LearnedRevision:
			it := led.Items[x.Item]
			if it == nil {
				if x.Predecessor != "" {
					return fmt.Errorf("learn: first revision %s of item %s names a predecessor", x.ID, x.Item)
				}
				it = &Item{ID: x.Item, Stage: map[record.RecordID]Stage{}, Transitions: map[record.RecordID][]*LifecycleTransition{}}
				led.Items[x.Item] = it
			} else {
				if x.Predecessor != it.Current.ID {
					return fmt.Errorf("learn: revision %s of item %s names predecessor %s but the current revision is %s", x.ID, x.Item, x.Predecessor, it.Current.ID)
				}
				if x.LearnedKind != it.Current.LearnedKind {
					return fmt.Errorf("learn: revision %s changes item %s from %s to %s", x.ID, x.Item, it.Current.LearnedKind, x.LearnedKind)
				}
			}
			it.Revisions = append(it.Revisions, x)
			it.Current = x
		case *LifecycleTransition:
			it := led.Items[x.Item]
			if it == nil || !hasRevision(it, x.Revision) {
				return fmt.Errorf("learn: transition %s for unknown revision %s of item %s", x.ID, x.Revision, x.Item)
			}
			if cur := it.StageOf(x.Revision); cur != x.From {
				return fmt.Errorf("learn: transition %s says %s→%s but revision %s is at %s", x.ID, x.From, x.To, x.Revision, cur)
			}
			if x.Evidence != "" && !seen[x.Evidence] {
				return fmt.Errorf("learn: transition %s cites evidence %s that is not an earlier record", x.ID, x.Evidence)
			}
			it.Stage[x.Revision] = x.To
			it.Transitions[x.Revision] = append(it.Transitions[x.Revision], x)
		case *Application:
			it := led.Items[x.Item]
			if it == nil || !hasRevision(it, x.Revision) {
				return fmt.Errorf("learn: application %s for unknown revision %s of item %s", x.ID, x.Revision, x.Item)
			}
			for _, a := range led.Applications[x.Invocation] {
				if a.Item == x.Item {
					return fmt.Errorf("learn: item %s applied twice to invocation %s", x.Item, x.Invocation)
				}
			}
			led.Applications[x.Invocation] = append(led.Applications[x.Invocation], x)
		case *RecallSelection:
			k := RecallKey(x.RunID, x.Attempt)
			if led.Recalls[k] != nil {
				return fmt.Errorf("learn: two recall selections for attempt %s", k)
			}
			for _, ir := range x.Included {
				it := led.Items[ir.Item]
				if it == nil || !hasRevision(it, ir.Revision) {
					return fmt.Errorf("learn: recall %s includes unknown revision %s of item %s", x.ID, ir.Revision, ir.Item)
				}
			}
			led.Recalls[k] = x
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return led, nil
}

func hasRevision(it *Item, rev record.RecordID) bool {
	for _, r := range it.Revisions {
		if r.ID == rev {
			return true
		}
	}
	return false
}

// Query is the one recall query (§7): purpose, the run's scope chain (own
// goal → parents → root → workspace), the goal's family, and which stages
// count as selectable.
type Query struct {
	Purpose  string
	Scope    []ScopePath
	Family   string
	Standing map[Stage]bool
}

// Recall is pure over the ledger. Every item is considered; each is either
// included (its CURRENT revision, only if that revision's own standing is
// selectable, in scope, of the family, and a lesson) or excluded with a
// reason. Order is by item id — deterministic and content-independent.
func Recall(led *Ledger, q Query) *RecallSelection {
	ids := make([]LearnedID, 0, len(led.Items))
	for id := range led.Items {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	inScope := map[ScopePath]bool{}
	for _, s := range q.Scope {
		inScope[s] = true
	}
	standing := make([]Stage, 0, len(q.Standing))
	for s := range q.Standing {
		standing = append(standing, s)
	}
	sort.Slice(standing, func(i, j int) bool { return standing[i] < standing[j] })
	sel := &RecallSelection{Scope: q.Scope, Family: q.Family, Standing: standing, ExcludedCounts: map[string]int{}}
	for _, id := range ids {
		it := led.Items[id]
		cur := it.Current
		sel.Considered++
		reason := ""
		switch {
		case cur.LearnedKind != Lesson:
			reason = "kind:" + string(cur.LearnedKind)
		case !q.Standing[it.StageOf(cur.ID)]:
			reason = "stage:" + string(it.StageOf(cur.ID))
		case !inScope[cur.Scope]:
			reason = "scope"
		case cur.Family != "" && cur.Family != q.Family:
			reason = "family"
		}
		if reason == "" {
			sel.Included = append(sel.Included, ItemRev{Item: id, Revision: cur.ID})
			sel.ProjectedBytes += cur.Text.Bytes + int64(len(bullet)+1)
			continue
		}
		sel.ExcludedCounts[reason]++
		if len(sel.ExcludedTop) < TopK {
			sel.ExcludedTop = append(sel.ExcludedTop, Excluded{ItemRev: ItemRev{Item: id, Revision: cur.ID}, Reason: reason})
		}
	}
	if len(sel.Included) > 0 {
		sel.ProjectedBytes += int64(len(heading))
	}
	return sel
}

const (
	heading = "\n\n## Recalled lessons\n"
	bullet  = "- "
)

// Rendered is one included item's exact bytes in the request.
type Rendered struct {
	ItemRev
	Representation []byte
}

// Render composes the recall block appended to a request: the heading and
// one bullet per included revision, each bullet being that revision's
// representation — the bytes an Application proves are in the request.
// Empty selection → empty block (nothing appended, nothing applied).
func Render(sel *RecallSelection, text func(ItemRev) ([]byte, error)) ([]byte, []Rendered, error) {
	if len(sel.Included) == 0 {
		return nil, nil, nil
	}
	block := []byte(heading)
	var reps []Rendered
	for _, ir := range sel.Included {
		t, err := text(ir)
		if err != nil {
			return nil, nil, err
		}
		rep := append(append([]byte(bullet), t...), '\n')
		block = append(block, rep...)
		reps = append(reps, Rendered{ItemRev: ir, Representation: rep})
	}
	return block, reps, nil
}
