package run

import (
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Lineage is the scope a goal joins when it follows a prior goal: the prior
// goal and the root of its lineage. Recall walks own → parent → root →
// workspace (scope()); lessons the tail mints for a run are scoped to the
// run's root, so a lineage's learning stays with the lineage until evidence
// promotes it (promotion is not in this slice).
type Lineage struct {
	Goal record.RecordID
	Root record.RecordID
}

// LineageOf resolves a run handle to the lineage a new goal would join by
// following it: the run's goal and that goal's root. A replay or fork goal
// cannot be followed — its lineage belongs to the unit or the parent step.
func LineageOf(led *Ledger, handle string) (*Lineage, error) {
	for _, rs := range led.Runs {
		if HandleOf(rs.Run) != handle {
			continue
		}
		g := rs.Goal
		if g.Origin == OriginReplay || g.Origin == OriginFork {
			return nil, fmt.Errorf("run: %s is a %s goal; follow the goal it descends from instead", handle, g.Origin)
		}
		return &Lineage{Goal: g.ID, Root: rs.Root}, nil
	}
	return nil, fmt.Errorf("run: no run with handle %s", handle)
}
