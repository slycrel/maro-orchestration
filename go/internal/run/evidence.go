package run

import (
	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// The judge's evidence, on the driver's side. Every function here derives
// from committed records only, through invoke.Digest, so the fold — which
// replays the same records into invoke.State — builds the same bytes
// (see checkJudgeVerdict). If the driver had a shortcut the fold lacked,
// a judge request would not re-derive, so there is none.

// outcomeEvidence is a just-made (or journal-reused) execute call's
// evidence: the records the shell committed for it.
func outcomeEvidence(o *invoke.Outcome, store *thought.Store) string {
	if o == nil || o.Invocation == "" {
		return invoke.EvidenceUnavailable
	}
	return invoke.Digest(o.Tools, o.EffectRecords, o.EffectResults, o.Terminal, o.Reason, store.Get, invoke.EvidenceMaxBytes)
}

// stateEvidence is an execute call's evidence from its replayed state.
func stateEvidence(st *invoke.State, store *thought.Store) string {
	if st == nil || st.Invocation == nil || st.Terminal == nil {
		return invoke.EvidenceUnavailable
	}
	return invoke.Digest(st.Invocation.Tools, st.Effects, st.Results, st.Terminal.State, st.Terminal.Reason, store.Get, invoke.EvidenceMaxBytes)
}

// stepEvidence is a committed step's evidence: by what the step was —
// gated (nothing ran), a fork (its members' runs hold it), or executed
// (its invocation's record, looked up by id).
func stepEvidence(sd *StepDone, members int, state func(record.RecordID) *invoke.State, store *thought.Store) string {
	switch {
	case sd == nil:
		return invoke.EvidenceUnavailable
	case sd.Outcome == StepGated:
		return invoke.GatedEvidence
	case sd.Fork != "":
		return invoke.ForkEvidence(members)
	case sd.Invocation == "":
		return invoke.EvidenceUnavailable
	}
	if state == nil {
		return invoke.EvidenceUnavailable
	}
	return stateEvidence(state(sd.Invocation), store)
}

// journalStates reads the execute invocations named, with their effects,
// results and terminals, straight from the production journal: the
// driver's lookup for a step it did not just run (a step done by an
// earlier attempt, or a NOW run's one call at closure). It reads only what
// the fold reads, so the digest agrees.
func journalStates(j *journal.Journal, ids map[record.RecordID]bool) (map[record.RecordID]*invoke.State, error) {
	out := map[record.RecordID]*invoke.State{}
	if len(ids) == 0 {
		return out, nil
	}
	get := func(id record.RecordID) *invoke.State {
		if !ids[id] {
			return nil
		}
		st := out[id]
		if st == nil {
			st = &invoke.State{Results: map[int]*invoke.ToolEffectResult{}}
			out[id] = st
		}
		return st
	}
	err := j.Production().Scan(0, func(r record.Record) error {
		switch v := r.(type) {
		case *invoke.Invocation:
			if st := get(v.ID); st != nil {
				st.Invocation = v
			}
		case *invoke.ToolEffect:
			if st := get(v.Invocation); st != nil {
				st.Effects = append(st.Effects, v)
			}
		case *invoke.ToolEffectResult:
			if st := get(v.Invocation); st != nil {
				st.Results[v.Ordinal] = v
			}
		case *invoke.TerminalObserved:
			if st := get(v.Invocation); st != nil && st.Terminal == nil {
				st.Terminal = v
			}
		}
		return nil
	})
	return out, err
}

// planEvidence is one evidence text per committed step of an attempt, in
// step order, from the attempt's step records and the invocation states
// they cite. Both the driver (at closure, with states read from the
// journal) and the fold (with the states it replayed) call it.
func planEvidence(a *AttemptState, forkMembers func(record.RecordID) int, state func(record.RecordID) *invoke.State, store *thought.Store) []string {
	out := make([]string, 0, len(a.Steps))
	for _, sd := range a.Steps {
		members := 0
		if sd.Fork != "" && forkMembers != nil {
			members = forkMembers(sd.Fork)
		}
		out = append(out, stepEvidence(sd, members, state, store))
	}
	return out
}

// journalForkMembers is each named fork's member count from its fork
// record in the production journal; a fork not found counts 0 (the fold
// says the same when it has no folded fork for the id).
func journalForkMembers(j *journal.Journal, ids map[record.RecordID]bool) (map[record.RecordID]int, error) {
	out := map[record.RecordID]int{}
	if len(ids) == 0 {
		return out, nil
	}
	err := j.Production().Scan(0, func(r record.Record) error {
		if f, ok := r.(*Fork); ok && ids[f.ID] {
			out[f.ID] = len(f.Members)
		}
		return nil
	})
	return out, err
}
