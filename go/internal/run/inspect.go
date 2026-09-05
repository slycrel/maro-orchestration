package run

import (
	"fmt"
	"sort"
	"strings"
)

// Inspect renders what decided a run's latest attempt, from the fold
// alone: the lens, the recall selection, the policy snapshot, every
// invocation with its request hash (the acceptance predicate's "request
// hash demonstrably changed"), and the metering target with its verdict.
func Inspect(rs *RunState) []string {
	a := rs.Latest()
	if a == nil {
		return []string{"no attempt yet"}
	}
	var lines []string
	arm := "none"
	if rs.Goal.Arm != nil {
		arm = string(rs.Goal.Arm.Arm) + " (assignment " + string(rs.Goal.Arm.Assignment) + ")"
	}
	lineage := "root of its own lineage"
	if rs.Parent != "" {
		lineage = fmt.Sprintf("follows %s, root %s", rs.Parent, rs.Root)
	}
	lines = append(lines, fmt.Sprintf("goal %s: family %s, origin %s, lane %s, arm %s, %s", rs.Goal.ID, rs.Family.Family, rs.Goal.Origin, rs.Goal.Lane, arm, lineage))
	if ls := rs.Landscape; ls != nil {
		chosen := ""
		if ls.Chosen != "" {
			chosen = " run " + HandleOf(ls.Chosen)
		}
		lines = append(lines, fmt.Sprintf("landscape: %s%s (%s; %d candidate(s) of %d scanned, %d below the floor)%s", ls.Relation, chosen, ls.Rule, len(ls.Candidates), ls.Scanned, ls.BelowFloor, map[bool]string{true: ": " + ls.Reason, false: ""}[ls.Reason != ""]))
	}
	lens := a.Attempt.Config.Lens
	if lens == "" {
		lens = LensNeutral
	}
	lines = append(lines, "lens: "+lens)
	if f := a.Attempt.Config.Frame; f != nil {
		lines = append(lines, "frame: "+f.Hash)
	} else {
		lines = append(lines, "frame: none (bare goal)")
	}
	if r := a.Recall; r != nil {
		var ex []string
		for k, v := range r.ExcludedCounts {
			ex = append(ex, fmt.Sprintf("%s=%d", k, v))
		}
		sort.Strings(ex)
		var inc []string
		for _, ir := range r.Included {
			inc = append(inc, string(ir.Revision))
		}
		lines = append(lines, fmt.Sprintf("recall %s: %d included of %d considered [%s] excluded: %s", r.ID, len(r.Included), r.Considered, strings.Join(inc, " "), strings.Join(ex, " ")))
	} else {
		lines = append(lines, "recall: none (the attempt did not reach it)")
	}
	if p := a.Policy; p != nil {
		var mech []string
		for m, on := range p.Snapshot {
			mech = append(mech, fmt.Sprintf("%s=%t", m, on))
		}
		sort.Strings(mech)
		lines = append(lines, fmt.Sprintf("policy %s: %d of %d enabled, %d excluded; mechanisms %s", p.ID, len(p.Enabled), len(p.Considered), len(p.Excluded), strings.Join(mech, " ")))
		// each exclusion as the selection recorded it: which revision, at
		// what stage, why — the absence proof is these entries, not the count
		for _, ex := range p.Excluded {
			lines = append(lines, fmt.Sprintf("  excluded %s (item %s, stage %s): %s", ex.Revision, ex.Item, ex.Stage, ex.Reason))
		}
	}
	for _, is := range a.Invocations {
		inv := is.Invocation
		l := ""
		if inv.Lens != nil {
			l = " lens=" + inv.Lens.Name
		}
		term := "open"
		if is.Terminal != nil {
			term = string(is.Terminal.State)
		}
		if inv.Cwd != "" {
			l += " cwd=" + inv.Cwd
		}
		if inv.Tools && inv.Backend.ToolPolicy != "" {
			l += " tools=" + inv.Backend.ToolPolicy
		}
		lines = append(lines, fmt.Sprintf("invocation %s: %s request=%s model=%s%s terminal=%s", inv.ID, inv.Purpose, inv.Request.Hash, inv.Backend.Model, l, term))
	}
	if t := rs.Target; t != nil {
		if rec := a.Has(Recorded); rec != nil {
			lines = append(lines, MeteringLine(t, rec.Outcome.Usage, a.Overage))
		} else {
			lines = append(lines, fmt.Sprintf("metering: %s — %s target %s (%s): not yet recorded", t.Name, t.Dimension, num(t.Limit), t.Why))
		}
	}
	return lines
}
