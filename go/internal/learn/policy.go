package learn

import (
	"errors"
	"fmt"
	"sort"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The policy apply surface (§7, D17). A policy item is versioned DATA the
// driver reads at one boundary — the start of an attempt — never code: a
// mechanism is on or off because a selectable policy revision says so, and
// the attempt's config snapshot carries the result. The selection is a
// derived record (the fold re-derives it, like a recall selection) and each
// enabled revision leaves a policy_application: the proof it reached the
// boundary, the way an application proves a lesson reached a request.

const (
	KindPolicySelection   record.Kind = "policy_selection"
	KindPolicyApplication record.Kind = "policy_application"
)

// Mechanism names one thing the harness does beyond a bare call. The
// vocabulary is closed: a policy naming an unknown mechanism is refused at
// the door, and a config snapshot must speak to every mechanism.
type Mechanism string

const (
	// MechRecall is recall injection: the recalled-lessons block appended to
	// execute requests. Off = the request is the goal alone, and the recall
	// selection records that nothing was considered selectable.
	MechRecall Mechanism = "recall"
	// MechModelJudge is the separate tool-less judge backend AGENDA's
	// per-step and closure judgments run on. Off = those calls run on the
	// executor backend itself, tool-less (the snapshot shows which).
	MechModelJudge Mechanism = "model_judge"
)

// Mechanisms is the vocabulary with its defaults: everything on until a
// policy says otherwise. Why on by default: the harness IS these
// mechanisms; a policy exists to switch one off on evidence (§8a
// item_redundant → tombstone → disabled), not to opt in.
var Mechanisms = map[Mechanism]bool{MechRecall: true, MechModelJudge: true}

// Defaults is a fresh copy of the default snapshot.
func Defaults() map[Mechanism]bool {
	m := make(map[Mechanism]bool, len(Mechanisms))
	for k, v := range Mechanisms {
		m[k] = v
	}
	return m
}

// PolicyRule is a policy revision's content: one mechanism, on or off. It
// is a record field, not a thought: a policy is process data the engine
// executes (D16 — process artifacts are contract-tested hard), so its
// vocabulary is declared and its unknown values are refused.
type PolicyRule struct {
	Mechanism Mechanism `json:"mechanism"`
	Enabled   bool      `json:"enabled"`
}

func (p *PolicyRule) validate() error {
	if p == nil {
		return errors.New("a policy revision carries its rule")
	}
	if _, ok := Mechanisms[p.Mechanism]; !ok {
		return fmt.Errorf("mechanism %q out of vocabulary", p.Mechanism)
	}
	return nil
}

// PolicySelection is the attempt's policy decision: every policy item
// considered (its current revision), the ones whose standing is selectable,
// the transitions that made them so, and the resulting mechanism snapshot.
// One per attempt, committed in the attempt's own command.
type PolicySelection struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Scope         []ScopePath        `json:"scope"`
	Family        string             `json:"family,omitempty"`
	Standing      []Stage            `json:"standing"`
	Considered    []ItemRev          `json:"considered,omitempty"` // policy items in scope, item order
	Enabled       []ItemRev          `json:"enabled,omitempty"`    // the selectable subset, same order
	Basis         []record.RecordID  `json:"basis,omitempty"`      // per enabled revision, the transition that made it selectable
	Snapshot      map[Mechanism]bool `json:"snapshot"`             // defaults, then each enabled rule in order
}

func (r *PolicySelection) Head() *record.Header { return &r.Header }
func (r *PolicySelection) Kind() record.Kind    { return KindPolicySelection }
func (r *PolicySelection) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.RunID == "" || r.Attempt == 0 {
		return errors.New("policy_selection: needs run_id and attempt")
	}
	if len(r.Scope) == 0 {
		return errors.New("policy_selection: needs a scope chain")
	}
	for _, s := range r.Scope {
		if err := validScope(s); err != nil {
			return fmt.Errorf("policy_selection: %w", err)
		}
	}
	for _, s := range r.Standing {
		if !stages[s] {
			return fmt.Errorf("policy_selection: standing %q out of vocabulary", s)
		}
	}
	for i, ir := range r.Considered {
		if err := record.ValidateID(record.RecordID(ir.Item)); err != nil {
			return fmt.Errorf("policy_selection: considered: %w", err)
		}
		if err := record.ValidateID(ir.Revision); err != nil {
			return fmt.Errorf("policy_selection: considered: %w", err)
		}
		if i > 0 && r.Considered[i-1].Item >= ir.Item {
			return errors.New("policy_selection: considered is not in item order")
		}
	}
	if len(r.Basis) != len(r.Enabled) {
		return errors.New("policy_selection: one basis per enabled revision")
	}
	for i, ir := range r.Enabled {
		if err := record.ValidateID(ir.Revision); err != nil {
			return fmt.Errorf("policy_selection: enabled: %w", err)
		}
		if err := record.ValidateID(r.Basis[i]); err != nil {
			return fmt.Errorf("policy_selection: basis: %w", err)
		}
	}
	if err := validSnapshot(r.Snapshot); err != nil {
		return fmt.Errorf("policy_selection: %w", err)
	}
	return nil
}

// validSnapshot: a snapshot speaks to exactly the vocabulary — an unknown
// mechanism is refused, a missing one is refused (silence would read as
// "on" to one reader and "off" to another).
func validSnapshot(s map[Mechanism]bool) error {
	if len(s) != len(Mechanisms) {
		return fmt.Errorf("snapshot names %d mechanisms, the vocabulary has %d", len(s), len(Mechanisms))
	}
	for m := range s {
		if _, ok := Mechanisms[m]; !ok {
			return fmt.Errorf("snapshot names mechanism %q, out of vocabulary", m)
		}
	}
	return nil
}

// PolicyApplication proves one enabled policy revision reached the
// attempt's boundary: which mechanism it set, to what. One per enabled
// revision of the selection, in the same command.
type PolicyApplication struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Item          LearnedID       `json:"item"`
	Revision      record.RecordID `json:"revision"`
	Selection     record.RecordID `json:"selection"`
	Rule          PolicyRule      `json:"rule"`
}

func (r *PolicyApplication) Head() *record.Header { return &r.Header }
func (r *PolicyApplication) Kind() record.Kind    { return KindPolicyApplication }
func (r *PolicyApplication) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.RunID == "" || r.Attempt == 0 {
		return errors.New("policy_application: needs run_id and attempt")
	}
	if err := record.ValidateID(record.RecordID(r.Item)); err != nil {
		return fmt.Errorf("policy_application: item: %w", err)
	}
	if err := record.ValidateID(r.Revision); err != nil {
		return fmt.Errorf("policy_application: revision: %w", err)
	}
	if err := record.ValidateID(r.Selection); err != nil {
		return fmt.Errorf("policy_application: selection: %w", err)
	}
	if r.Subject.Kind != "policy_selection" || r.Subject.ID != string(r.Selection) {
		return errors.New("policy_application: subject must be the selection")
	}
	if err := r.Rule.validate(); err != nil {
		return fmt.Errorf("policy_application: %w", err)
	}
	return nil
}

// PolicyKey names an attempt's policy selection in the ledger.
func PolicyKey(run record.RunID, attempt uint32) string { return RecallKey(run, attempt) }

// SelectPolicy is pure over the ledger: every policy item in scope and of
// the family is considered; its current revision is enabled only if that
// revision's own standing is selectable. Rules apply over the defaults in
// item order — deterministic, content-independent, and the same order the
// fold re-derives.
func SelectPolicy(led *Ledger, q Query) *PolicySelection {
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
	sel := &PolicySelection{Scope: q.Scope, Family: q.Family, Standing: standing, Snapshot: Defaults()}
	for _, id := range ids {
		it := led.Items[id]
		cur := it.Current
		if cur.LearnedKind != Policy || !inScope[cur.Scope] || (cur.Family != "" && cur.Family != q.Family) {
			continue
		}
		ir := ItemRev{Item: id, Revision: cur.ID}
		sel.Considered = append(sel.Considered, ir)
		if !q.Standing[it.StageOf(cur.ID)] {
			continue
		}
		trs := it.Transitions[cur.ID]
		sel.Enabled = append(sel.Enabled, ir)
		sel.Basis = append(sel.Basis, trs[len(trs)-1].ID) // selectable ⇒ at least one transition (candidate never is)
		sel.Snapshot[cur.Policy.Mechanism] = cur.Policy.Enabled
	}
	return sel
}

// Applications derives the policy applications a selection owes: one per
// enabled revision, in order, carrying that revision's rule.
func (led *Ledger) PolicyRules(sel *PolicySelection) []PolicyRule {
	out := make([]PolicyRule, 0, len(sel.Enabled))
	for _, ir := range sel.Enabled {
		out = append(out, *revisionOf(led.Items[ir.Item], ir.Revision).Policy)
	}
	return out
}

func revisionOf(it *Item, rev record.RecordID) *LearnedRevision {
	if it == nil {
		return nil
	}
	for _, r := range it.Revisions {
		if r.ID == rev {
			return r
		}
	}
	return nil
}

// checkPolicy executes the derived-record rule for a selection against the
// ledger as it stands.
func (led *Ledger) checkPolicy(x *PolicySelection) error {
	standing := map[Stage]bool{}
	for _, s := range x.Standing {
		standing[s] = true
	}
	if !sameStages(standing, Selectable) {
		return fmt.Errorf("learn: policy selection %s used standing %v, not the selectable set", x.ID, x.Standing)
	}
	want := SelectPolicy(led, Query{Scope: x.Scope, Family: x.Family, Standing: standing})
	if !samePolicy(want, x) {
		return fmt.Errorf("learn: policy selection %s does not re-derive from the ledger (enabled %v vs %v; snapshot %v vs %v)", x.ID, x.Enabled, want.Enabled, x.Snapshot, want.Snapshot)
	}
	return nil
}

func samePolicy(a, b *PolicySelection) bool {
	if len(a.Considered) != len(b.Considered) || len(a.Enabled) != len(b.Enabled) || len(a.Snapshot) != len(b.Snapshot) {
		return false
	}
	for i := range a.Considered {
		if a.Considered[i] != b.Considered[i] {
			return false
		}
	}
	for i := range a.Enabled {
		if a.Enabled[i] != b.Enabled[i] || a.Basis[i] != b.Basis[i] {
			return false
		}
	}
	for m, v := range a.Snapshot {
		if w, ok := b.Snapshot[m]; !ok || w != v {
			return false
		}
	}
	return true
}
