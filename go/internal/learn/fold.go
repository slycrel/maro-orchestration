package learn

import (
	"fmt"
	"sort"
	"strings"

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
	Applications map[record.RecordID][]*Application       // by invocation, in Seq order
	Recalls      map[string]*RecallSelection              // by "<run>/<attempt>"
	Policies     map[string]*PolicySelection              // by "<run>/<attempt>"
	PolicyApps   map[record.RecordID][]*PolicyApplication // by selection, in Seq order
	Exposures    map[record.RecordID][]Exposure           // by revision, in Seq order: applications + policy applications
	byID         map[record.RecordID]*RecallSelection
	policyByID   map[record.RecordID]*PolicySelection
}

// PolicyOf returns a policy selection by id.
func (led *Ledger) PolicyOf(id record.RecordID) *PolicySelection { return led.policyByID[id] }

// Selection returns a recall selection by id.
func (led *Ledger) Selection(id record.RecordID) *RecallSelection { return led.byID[id] }

// checkRecall executes the derived-record rule for a selection against the
// ledger as it stands (the scan is in Seq order, so "as it stood" holds).
func (led *Ledger) checkRecall(x *RecallSelection) error {
	// the recall obeys THIS attempt's policy selection, which precedes it
	pol := led.Policies[PolicyKey(x.RunID, x.Attempt)]
	if pol == nil || pol.ID != x.Policy {
		return fmt.Errorf("learn: recall %s names policy selection %s, which is not the attempt's", x.ID, x.Policy)
	}
	off := !pol.Snapshot[MechRecall]
	if x.Continues != "" {
		prior := led.byID[x.Continues]
		if prior == nil || prior.RunID != x.RunID || prior.Attempt >= x.Attempt {
			return fmt.Errorf("learn: recall %s continues %s, which is not an earlier selection of the same run", x.ID, x.Continues)
		}
		if !sameSelection(prior, x) {
			return fmt.Errorf("learn: recall %s claims to continue %s but its content differs", x.ID, x.Continues)
		}
		if priorPol := led.policyByID[prior.Policy]; priorPol == nil || !priorPol.Snapshot[MechRecall] != off {
			return fmt.Errorf("learn: recall %s continues %s, which was decided under a different recall policy", x.ID, x.Continues)
		}
		return nil
	}
	standing := map[Stage]bool{}
	for _, s := range x.Standing {
		standing[s] = true
	}
	if x.Purpose == "execute" && !sameStages(standing, Selectable) {
		return fmt.Errorf("learn: recall %s for execute used standing %v, not the selectable set", x.ID, x.Standing)
	}
	want := Recall(led, Query{Purpose: string(x.Purpose), Scope: x.Scope, Family: x.Family, Standing: standing, Off: off, Arm: x.Arm})
	if !sameSelection(want, x) {
		return fmt.Errorf("learn: recall %s does not re-derive from the ledger (included %v vs %v; considered %d vs %d; excluded %v vs %v)", x.ID, x.Included, want.Included, x.Considered, want.Considered, x.ExcludedCounts, want.ExcludedCounts)
	}
	return nil
}

func sameStages(a, b map[Stage]bool) bool {
	if len(a) != len(b) {
		return false
	}
	for s := range a {
		if !b[s] {
			return false
		}
	}
	return true
}

// sameSelection compares every derived field.
func sameSelection(a, b *RecallSelection) bool {
	if a.Considered != b.Considered || a.ProjectedBytes != b.ProjectedBytes || len(a.Included) != len(b.Included) || len(a.ExcludedCounts) != len(b.ExcludedCounts) || len(a.ExcludedSample) != len(b.ExcludedSample) {
		return false
	}
	for i := range a.Included {
		if a.Included[i] != b.Included[i] {
			return false
		}
	}
	for k, v := range a.ExcludedCounts {
		if b.ExcludedCounts[k] != v {
			return false
		}
	}
	for i := range a.ExcludedSample {
		if a.ExcludedSample[i] != b.ExcludedSample[i] {
			return false
		}
	}
	return true
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
	pr = pr.Pin() // one prefix for every scan this fold composes
	led := &Ledger{Items: map[LearnedID]*Item{}, Applications: map[record.RecordID][]*Application{}, Recalls: map[string]*RecallSelection{}, Policies: map[string]*PolicySelection{}, PolicyApps: map[record.RecordID][]*PolicyApplication{}, Exposures: map[record.RecordID][]Exposure{}, byID: map[record.RecordID]*RecallSelection{}, policyByID: map[record.RecordID]*PolicySelection{}}
	seen := map[record.RecordID]bool{}
	goals := map[record.RecordID]bool{}
	evidence := map[record.RecordID]EffectEvidence{}
	expose := func(h record.Header, rev record.RecordID) {
		led.Exposures[rev] = append(led.Exposures[rev], Exposure{ID: h.ID, Revision: rev, Seq: h.Seq, At: h.At})
	}
	err := pr.Scan(0, func(r record.Record) error {
		// a record is "seen" only AFTER its own checks: nothing may cite itself
		defer func() { seen[r.Head().ID] = true }()
		if r.Kind() == "goal" {
			goals[r.Head().ID] = true
		}
		if ev, ok := r.(EffectEvidence); ok {
			evidence[r.Head().ID] = ev
		}
		switch x := r.(type) {
		case *LearnedRevision:
			if strings.HasPrefix(string(x.Scope), "goal:") && !goals[record.RecordID(strings.TrimPrefix(string(x.Scope), "goal:"))] {
				return fmt.Errorf("learn: revision %s is scoped to goal %s, which is not an earlier record", x.ID, strings.TrimPrefix(string(x.Scope), "goal:"))
			}
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
			if x.Actor == ActorMeasurement {
				// a measurement transition is DERIVED from its evidence: the
				// evidence names THIS revision, and the edge is StageFor's
				ev := evidence[x.Evidence]
				if ev == nil {
					return fmt.Errorf("learn: measurement transition %s cites %s, which is not effect evidence", x.ID, x.Evidence)
				}
				ir, eff := ev.Effect()
				if ir != (ItemRev{Item: x.Item, Revision: x.Revision}) {
					return fmt.Errorf("learn: measurement transition %s moves %s/%s on evidence about %s/%s", x.ID, x.Item, x.Revision, ir.Item, ir.Revision)
				}
				if want, ok := StageFor(x.From, eff); !ok || want != x.To {
					return fmt.Errorf("learn: measurement transition %s says %s→%s but %s from %s derives %q", x.ID, x.From, x.To, eff, x.From, want)
				}
			}
			if x.Actor == ActorTenure {
				// a tenure transition is DERIVED: it must equal the timers'
				// rule over the exposures as they stood
				exps := led.Exposures[x.Revision]
				switch x.To {
				case Observed:
					if len(exps) < TenureBound || exps[TenureBound-1].ID != x.Evidence {
						return fmt.Errorf("learn: tenure transition %s does not re-derive: evidence must be exposure %d of revision %s (has %d)", x.ID, TenureBound, x.Revision, len(exps))
					}
				case Tombstone:
					rev := revisionOf(it, x.Revision)
					if x.Evidence != x.Revision || !x.At.After(LastActivity(rev, exps).Add(ExpiryIdle)) {
						return fmt.Errorf("learn: expiry transition %s does not re-derive: revision %s was active within %s of it", x.ID, x.Revision, ExpiryIdle)
					}
				}
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
			expose(x.Header, x.Revision)
		case *PolicySelection:
			k := PolicyKey(x.RunID, x.Attempt)
			if led.Policies[k] != nil {
				return fmt.Errorf("learn: two policy selections for attempt %s", k)
			}
			// a DERIVED record: it must equal the selection over the ledger
			// as it stood
			if err := led.checkPolicy(x); err != nil {
				return err
			}
			led.Policies[k] = x
			led.policyByID[x.ID] = x
		case *PolicyApplication:
			sel := led.policyByID[x.Selection]
			if sel == nil || sel.RunID != x.RunID || sel.Attempt != x.Attempt {
				return fmt.Errorf("learn: policy application %s cites selection %s, which is not its attempt's", x.ID, x.Selection)
			}
			have := led.PolicyApps[x.Selection]
			if len(have) >= len(sel.Enabled) || sel.Enabled[len(have)] != (ItemRev{Item: x.Item, Revision: x.Revision}) {
				return fmt.Errorf("learn: policy application %s is not enabled revision %d of selection %s", x.ID, len(have)+1, x.Selection)
			}
			if rule := revisionOf(led.Items[x.Item], x.Revision).Policy; rule == nil || *rule != x.Rule {
				return fmt.Errorf("learn: policy application %s carries a rule that is not revision %s's", x.ID, x.Revision)
			}
			led.PolicyApps[x.Selection] = append(have, x)
			expose(x.Header, x.Revision)
		case *RecallSelection:
			k := RecallKey(x.RunID, x.Attempt)
			if led.Recalls[k] != nil {
				return fmt.Errorf("learn: two recall selections for attempt %s", k)
			}
			// a selection is a DERIVED record: it must equal the query's
			// result over the ledger as it stood, or equal the earlier
			// selection it continues
			if err := led.checkRecall(x); err != nil {
				return err
			}
			led.Recalls[k] = x
			led.byID[x.ID] = x
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	// every selection's enabled revisions were applied (same command, so a
	// prefix never splits them): the snapshot is only as true as its proofs
	for k, sel := range led.Policies {
		if n := len(led.PolicyApps[sel.ID]); n != len(sel.Enabled) {
			return nil, fmt.Errorf("learn: policy selection of attempt %s enables %d revisions but %d were applied", k, len(sel.Enabled), n)
		}
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
	// Off is the recall mechanism switched off by policy: every item is
	// considered and excluded (reason policy:recall_off), so the selection
	// still says what WOULD have been recalled and why it was not.
	Off bool
	// Arm is an experiment arm's forced sets (nil for a production run).
	Arm *ArmRef
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
	sel := &RecallSelection{Scope: q.Scope, Family: q.Family, Standing: standing, ExcludedCounts: map[string]int{}, Arm: q.Arm}
	for _, id := range ids {
		it := led.Items[id]
		cur := it.Current
		sel.Considered++
		reason := ""
		forced := q.forced(ItemRev{Item: id, Revision: cur.ID})
		switch {
		case q.Off:
			reason = "policy:recall_off"
		case cur.LearnedKind != Lesson:
			reason = "kind:" + string(cur.LearnedKind)
		case forced == "withhold":
			reason = "arm:withheld"
		case forced == "apply":
			// the arm applies it regardless of standing, scope, or family
		case !q.Standing[it.StageOf(cur.ID)]:
			reason = "stage:" + string(it.StageOf(cur.ID))
		case !inScope[cur.Scope]:
			reason = "scope"
		case cur.Family != "" && cur.Family != q.Family:
			reason = "family"
		}
		if reason == "" {
			sel.Included = append(sel.Included, ItemRev{Item: id, Revision: cur.ID})
			sel.ProjectedBytes += cur.Text.Bytes + int64(len(bullet)+1) // continuation indents are not projected: a projection, not a cap
			continue
		}
		sel.ExcludedCounts[reason]++
		if len(sel.ExcludedSample) < SampleK {
			sel.ExcludedSample = append(sel.ExcludedSample, Excluded{ItemRev: ItemRev{Item: id, Revision: cur.ID}, Reason: reason})
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
// A lesson's continuation lines are indented under its bullet, so a text
// that contains "- " or "## " lines cannot pose as further bullets or a
// new section: one item, one bullet, whatever the text holds. Empty
// selection → empty block (nothing appended, nothing applied).
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
		rep := append([]byte(bullet), Frame(t)...)
		rep = append(rep, '\n')
		block = append(block, rep...)
		reps = append(reps, Rendered{ItemRev: ir, Representation: rep})
	}
	return block, reps, nil
}

// Frame indents every line after the first so the text stays inside its
// bullet. Bytes are otherwise untouched (D16).
func Frame(t []byte) []byte {
	return []byte(strings.ReplaceAll(strings.TrimRight(string(t), "\n"), "\n", "\n  "))
}
