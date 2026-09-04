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
// canon) on a seed revision, citing the revision itself. Nothing else.
const ActorSeed Actor = "seed"

// SourceSeed is the provenance source of a seed revision.
const SourceSeed = "seed"

// SeedKey is the idempotency key of the seed command: one per workspace,
// so a second process (or a second attempt) finds the ack and writes
// nothing.
const SeedKey = "learn/seeds/1"

// SeedRecords is the seed command's body, deterministic in mechanism order:
// each mechanism's revision followed by its two transitions.
func SeedRecords() []record.Record {
	mechs := make([]Mechanism, 0, len(Mechanisms))
	for m := range Mechanisms {
		mechs = append(mechs, m)
	}
	sort.Slice(mechs, func(i, j int) bool { return mechs[i] < mechs[j] })
	at := time.Now().UTC()
	var out []record.Record
	for _, m := range mechs {
		item := LearnedID(record.NewID())
		sub := record.Ref{Kind: "learned", ID: string(item)}
		r := &LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: sub, At: at},
			Item: item, LearnedKind: Policy, Scope: ScopeWorkspace, Policy: &PolicyRule{Mechanism: m, Enabled: true},
			Provenance: Provenance{Source: SourceSeed, Why: fmt.Sprintf("harness default: %s on, as data an experiment can ablate", m)}}
		out = append(out, r)
		for _, edge := range [][2]Stage{{Candidate, Effective}, {Effective, Canon}} {
			out = append(out, &LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: sub, At: at},
				Item: item, Revision: r.ID, From: edge[0], To: edge[1], Actor: ActorSeed, Evidence: r.ID, Why: "seed: the harness default is canon until measured otherwise"})
		}
	}
	return out
}

// EnsureSeeds commits the seed command if the journal has not seen it.
// Idempotent across processes: the key's ack is recovered from the file.
func EnsureSeeds(ctx context.Context, j *journal.Journal) error {
	_, err := j.Submit(ctx, journal.Command{IdempotencyKey: SeedKey, Epoch: j.Epoch(), Records: SeedRecords()})
	return err
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

// checkSeedTransition: the seed actor moves a seed revision along its two
// edges citing the revision; nobody else uses that actor.
func checkSeedTransition(x *LifecycleTransition, rev *LearnedRevision) error {
	if x.Actor != ActorSeed {
		return nil
	}
	if !IsSeed(rev) || x.Evidence != x.Revision {
		return fmt.Errorf("learn: seed transition %s on %s, which is not a seed revision citing itself", x.ID, x.Revision)
	}
	return nil
}
