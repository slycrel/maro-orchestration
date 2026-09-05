package learn

import (
	"context"
	"fmt"
	"sort"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Seeds: the harness's own defaults as DATA (D17). Every mechanism is off
// until a selectable policy revision says on; the seeds are those
// revisions — one canon policy item per mechanism, committed once at the
// first policy boundary a workspace reaches. Because a seed is an item
// like any other, an `ablate(m)` experiment withholds it (the mechanism
// goes off for the treatment arm and nothing else changes), an
// `equivalent` measurement tombstones it, and the next PolicySelection
// disables the mechanism with the tombstone as its absence proof — the
// harness lost a piece of itself to evidence, through the same door a
// lesson goes through.

// ActorSeed makes exactly the seed's two edges (candidate → effective →
// canon) on a seed revision, citing the revision itself. Nothing else —
// and nobody else makes those edges on a seed.
const ActorSeed Actor = "seed"

// SourceSeed is the provenance source of a seed revision.
const SourceSeed = "seed"

// SeedKeyFor is the idempotency key of a mechanism's seed command: one
// per workspace and mechanism, so a second process (or a second attempt)
// finds the ack and writes nothing, and a mechanism added by a later
// binary gets its own command instead of hiding behind an old ack.
func SeedKeyFor(m Mechanism) string { return "learn/seed/" + string(m) + "/1" }

// SeedRecordsFor is a mechanism's seed command: the revision followed by
// its two transitions.
func SeedRecordsFor(m Mechanism) []record.Record {
	at := time.Now().UTC()
	item := LearnedID(record.NewID())
	sub := record.Ref{Kind: "learned", ID: string(item)}
	r := &LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: sub, At: at},
		Item: item, LearnedKind: Policy, Scope: ScopeWorkspace, Policy: &PolicyRule{Mechanism: m, Enabled: true},
		Provenance: Provenance{Source: SourceSeed, Why: fmt.Sprintf("harness default: %s on, as data an experiment can ablate", m)}}
	out := []record.Record{r}
	for _, edge := range [][2]Stage{{Candidate, Effective}, {Effective, Canon}} {
		out = append(out, &LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: sub, At: at},
			Item: item, Revision: r.ID, From: edge[0], To: edge[1], Actor: ActorSeed, Evidence: r.ID, Why: "seed: the harness default is canon until measured otherwise"})
	}
	return out
}

// SeedRecords is every mechanism's seed command body, in mechanism order.
func SeedRecords() []record.Record {
	var out []record.Record
	for _, m := range mechanisms() {
		out = append(out, SeedRecordsFor(m)...)
	}
	return out
}

func mechanisms() []Mechanism {
	mechs := make([]Mechanism, 0, len(Mechanisms))
	for m := range Mechanisms {
		mechs = append(mechs, m)
	}
	sort.Slice(mechs, func(i, j int) bool { return mechs[i] < mechs[j] })
	return mechs
}

// EnsureSeeds folds the ledger and commits a seed command for every
// mechanism that has none, returning the ledger as it stands afterwards.
// A seeded workspace costs one fold and no write; a workspace seeded by an
// older binary gains the new mechanisms' seeds; a workspace whose seed for
// a mechanism is a forgery keeps it (the fold says what it is) — the
// honest seed is not written beside it, so the fold stays whole.
func EnsureSeeds(ctx context.Context, j *journal.Journal) (*Ledger, error) {
	led, err := Fold(j.Production())
	if err != nil {
		return nil, err
	}
	wrote := false
	for _, m := range mechanisms() {
		if led.Seed(m) != nil {
			continue
		}
		if _, err := j.Submit(ctx, journal.Command{IdempotencyKey: SeedKeyFor(m), Epoch: j.Epoch(), Records: SeedRecordsFor(m)}); err != nil {
			return nil, err
		}
		wrote = true
	}
	if wrote {
		return Fold(j.Production())
	}
	return led, nil
}

// Seed is the seed item of a mechanism, nil until the seeds are committed.
func (led *Ledger) Seed(m Mechanism) *Item {
	if id, ok := led.seeds[m]; ok {
		return led.Items[id]
	}
	return nil
}

// IsSeed reports whether a revision is a seed.
func IsSeed(r *LearnedRevision) bool { return r != nil && r.Provenance.Source == SourceSeed }

// checkSeedRevision executes the seed rules on a revision: a seed is a
// first, unrevised, workspace-scoped, any-family policy revision that
// turns its mechanism ON, and there is one per mechanism.
func (led *Ledger) checkSeedRevision(x *LearnedRevision, it *Item) error {
	if IsSeed(x) {
		if x.LearnedKind != Policy || x.Predecessor != "" || x.Scope != ScopeWorkspace || x.Family != "" || x.Policy == nil || !x.Policy.Enabled {
			return fmt.Errorf("learn: seed revision %s is not a first workspace policy revision turning its mechanism on", x.ID)
		}
		if prior, ok := led.seeds[x.Policy.Mechanism]; ok {
			return fmt.Errorf("learn: a second seed %s for mechanism %s (the seed is item %s)", x.ID, x.Policy.Mechanism, prior)
		}
		led.seeds[x.Policy.Mechanism] = x.Item
		return nil
	}
	if it != nil && len(it.Revisions) > 0 && IsSeed(it.Revisions[0]) {
		return fmt.Errorf("learn: revision %s revises seed item %s; a seed is never revised, only ablated", x.ID, x.Item)
	}
	return nil
}

// checkSeedTransition: a seed revision's promotion (from candidate, from
// effective) is the seed actor's exact edge citing the revision — no other
// actor promotes a seed, so a hand-written revision with seed provenance
// never becomes selectable; from canon on, any actor but the seed actor
// moves it (a measurement tombstones it, an operator demotes it). The seed
// actor touches nothing that is not a seed.
func checkSeedTransition(x *LifecycleTransition, rev *LearnedRevision) error {
	if !IsSeed(rev) {
		if x.Actor == ActorSeed {
			return fmt.Errorf("learn: seed transition %s on %s, which is not a seed revision", x.ID, x.Revision)
		}
		return nil
	}
	seedEdge := (x.From == Candidate && x.To == Effective) || (x.From == Effective && x.To == Canon)
	switch {
	case x.From == Candidate || x.From == Effective:
		if x.Actor != ActorSeed || !seedEdge || x.Evidence != x.Revision {
			return fmt.Errorf("learn: transition %s promotes seed %s by %s %s→%s; a seed's promotion is the seed actor's edge citing the revision", x.ID, x.Revision, x.Actor, x.From, x.To)
		}
	case x.Actor == ActorSeed:
		return fmt.Errorf("learn: seed transition %s moves seed %s from %s; the seed actor only promotes", x.ID, x.Revision, x.From)
	}
	return nil
}
