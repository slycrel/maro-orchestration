package main

import (
	"context"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/experiment"
	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	spine "github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// cmdExperiment is the operator surface of the measured loop (step 10b):
//
//	experiment open --item <id> [--revision <rev>] --relation apply|ablate --unit <goal>=<expected> ... [--margin f] [--why text]
//	experiment open --item <id> [--revision <rev>] --relation apply|ablate --live --population <family> --n <k> [--min-per-arm k] [--margin f] [--why text]
//	experiment open --mechanism recall|model_judge --relation ablate --live ...   (the mechanism's seed is the hypothesis)
//	experiment run <exp> [--model m] [--judge-model m]           (paired replay)
//	experiment close <exp> [--judge-model m]                     (a live cohort is scored by the judge)
//	experiment list | show <exp>
func cmdExperiment(args []string, out, errw io.Writer) error {
	if len(args) < 1 {
		return fmt.Errorf("experiment needs open|run|close|list|show")
	}
	return withJournal(out, func(j *journal.Journal, st *thought.Store) error {
		switch args[0] {
		case "open":
			return experimentOpen(args[1:], j, st, out)
		case "run":
			return experimentRun(args[1:], j, st, out, errw)
		case "close":
			if len(args) < 2 {
				return fmt.Errorf("experiment close <exp> [--judge-model m]")
			}
			judgeModel := "haiku"
			for i := 2; i < len(args); i++ {
				if args[i] == "--judge-model" && i+1 < len(args) {
					i++
					judgeModel = args[i]
				}
			}
			jb, err := invoke.NewSubprocess(judgeModel)
			if err != nil {
				return err
			}
			c := &experiment.Closer{J: j, Store: st, Judge: jb, Timeout: 5 * time.Minute, Events: func(l string) { fmt.Fprintln(errw, l) }}
			m, err := c.Close(context.Background(), record.RecordID(args[1]))
			if err != nil {
				return err
			}
			fmt.Fprintf(out, "closed %s: %s → %s (assigned %d, analyzed %d, exposed %d, discordant %d, delta_itt %.3f, delta_pp %.3f)\n", args[1], m.Verdict, m.ItemEffect, m.Assigned, m.Analyzed, m.Exposed, m.Discordant, m.DeltaITT, m.DeltaPP)
			state, err := experiment.Fold(j, st)
			if err != nil {
				return err
			}
			it := state.Runs.Learned.Items[m.Hypothesis.Item]
			fmt.Fprintf(out, "%s revision %s now %s\n", m.Hypothesis.Item, m.Hypothesis.Revision, it.StageOf(m.Hypothesis.Revision))
			return nil
		case "list", "show":
			state, err := experiment.Fold(j, st)
			if err != nil {
				return err
			}
			for _, id := range state.Order {
				if args[0] == "show" && (len(args) < 2 || string(id) != args[1]) {
					continue
				}
				x := state.Experiments[id]
				status := "open"
				if state.Closed[id] != nil {
					status = "closed"
				}
				if m := state.Measurements[id]; m != nil {
					status = fmt.Sprintf("measured %s (%s)", m.ItemEffect, m.Verdict)
				}
				assigned := 0
				evidence := 0
				for _, u := range x.Units {
					if as := state.Assignments[assignmentOf(state, id, u.Goal)]; as != nil {
						assigned++
						evidence += len(state.Evidence[as.ID])
					}
				}
				arms := 2 * x.N
				if x.Assignment == experiment.RandomizedLive {
					arms = x.N
					for _, as := range state.Assignments {
						if as.Experiment == id {
							assigned++
							evidence += len(state.Evidence[as.ID])
						}
					}
				}
				fmt.Fprintf(out, "%s  v%d %s %s %s/%s over %s  n=%d assigned=%d evidence=%d/%d  %s\n", id, x.Version, x.Assignment, x.Relation, x.Hypothesis.Item, x.Hypothesis.Revision, x.Population, x.N, assigned, evidence, arms, status)
				if args[0] == "show" && x.Assignment == experiment.RandomizedLive {
					for i := 0; ; i++ {
						as := assignmentAt(state, id, i)
						if as == nil {
							break
						}
						line := fmt.Sprintf("  unit %d %s  arm %s", i, as.Unit, as.Arm)
						if ev := state.Evidence[as.ID][as.Arm]; ev != nil {
							d := "missing:" + ev.Missing
							if ev.Deliverable != nil {
								b, _ := st.Get(*ev.Deliverable)
								d = fmt.Sprintf("%q", firstLine(string(b)))
							}
							line += fmt.Sprintf("  run %s exposed=%v %s", ev.RunID, ev.Exposed, d)
						}
						fmt.Fprintln(out, line)
					}
					if att := state.Attestations[id]; att != nil {
						for _, row := range att.Units {
							fmt.Fprintf(out, "  row %s  %s score=%.0f missing=%q as_assigned=%v\n", row.Unit, row.Arm, row.Score, row.Missing, row.Exposed)
						}
					}
				}
				if args[0] == "show" && x.Assignment == experiment.PairedReplay {
					for i, u := range x.Units {
						fx, _ := st.Get(u.Fixture)
						line := fmt.Sprintf("  unit %d %s  fixture %q", i, u.Goal, string(fx))
						if as := state.Assignments[assignmentOf(state, id, u.Goal)]; as != nil {
							for _, arm := range []string{experiment.Treatment, experiment.Control} {
								if ev := state.Evidence[as.ID][arm]; ev != nil {
									d := "missing:" + ev.Missing
									if ev.Deliverable != nil {
										b, _ := st.Get(*ev.Deliverable)
										d = fmt.Sprintf("%q", firstLine(string(b)))
									}
									line += fmt.Sprintf("\n    %-9s run %s exposed=%v %s", arm, ev.RunID, ev.Exposed, d)
								}
							}
						}
						fmt.Fprintln(out, line)
					}
					if att := state.Attestations[id]; att != nil {
						for _, row := range att.Units {
							fmt.Fprintf(out, "  row %s  treatment=%.0f control=%.0f exposed=%v\n", row.Unit, row.TreatmentScore, row.ControlScore, row.Exposed)
						}
					}
				}
			}
			return nil
		}
		return fmt.Errorf("unknown experiment subcommand %q", args[0])
	})
}

func assignmentAt(state *experiment.State, exp record.RecordID, ordinal int) *experiment.Assignment {
	for _, as := range state.Assignments {
		if as.Experiment == exp && as.Ordinal == ordinal {
			return as
		}
	}
	return nil
}

func assignmentOf(state *experiment.State, exp, unit record.RecordID) record.RecordID {
	for _, as := range state.Assignments {
		if as.Experiment == exp && as.Unit == unit {
			return as.ID
		}
	}
	return ""
}

func firstLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i] + "…"
	}
	return s
}

func experimentOpen(args []string, j *journal.Journal, st *thought.Store, out io.Writer) error {
	spec := experiment.Spec{Relation: experiment.ApplyItem, Why: "maro-go experiment open"}
	var item, rev, mechanism string
	intFlag := func(v string, dst *int) error {
		_, err := fmt.Sscanf(v, "%d", dst)
		return err
	}
	for i := 0; i < len(args); i++ {
		next := func() string {
			i++
			if i < len(args) {
				return args[i]
			}
			return ""
		}
		switch args[i] {
		case "--item":
			item = next()
		case "--mechanism":
			mechanism = next()
		case "--revision":
			rev = next()
		case "--relation":
			switch next() {
			case "apply":
				spec.Relation = experiment.ApplyItem
			case "ablate":
				spec.Relation = experiment.AblateItem
			default:
				return fmt.Errorf("--relation apply|ablate")
			}
		case "--unit":
			goal, expected, ok := strings.Cut(next(), "=")
			if !ok || goal == "" || strings.TrimSpace(expected) == "" {
				return fmt.Errorf("--unit <goal-id>=<expected answer text>")
			}
			ref, err := st.Put(thought.Fixture, []byte(strings.TrimSpace(expected)))
			if err != nil {
				return err
			}
			spec.Units = append(spec.Units, experiment.UnitSpec{Goal: record.RecordID(goal), Fixture: ref})
		case "--live":
			spec.Live = true
		case "--population":
			spec.Population = next()
		case "--n":
			if err := intFlag(next(), &spec.N); err != nil {
				return fmt.Errorf("--n: %w", err)
			}
		case "--min-per-arm":
			if err := intFlag(next(), &spec.MinPerArm); err != nil {
				return fmt.Errorf("--min-per-arm: %w", err)
			}
		case "--margin":
			if _, err := fmt.Sscanf(next(), "%g", &spec.Margin); err != nil {
				return fmt.Errorf("--margin: %w", err)
			}
		case "--why":
			spec.Why = next()
		default:
			return fmt.Errorf("unknown flag %q", args[i])
		}
	}
	if (item == "") == (mechanism == "") {
		return fmt.Errorf("experiment open needs --item <learned id> or --mechanism <m>")
	}
	if err := learn.EnsureSeeds(context.Background(), j); err != nil {
		return err
	}
	led, err := learn.Fold(j.Production())
	if err != nil {
		return err
	}
	var it *learn.Item
	if mechanism != "" {
		if it = led.Seed(learn.Mechanism(mechanism)); it == nil {
			return fmt.Errorf("no seed for mechanism %q (vocabulary: recall, model_judge)", mechanism)
		}
	} else if it = led.Items[learn.LearnedID(item)]; it == nil {
		return fmt.Errorf("no learned item %s", item)
	}
	if rev == "" {
		rev = string(it.Current.ID)
	}
	spec.Hypothesis = learn.ItemRev{Item: it.ID, Revision: record.RecordID(rev)}
	x, err := experiment.Open(context.Background(), j, st, spec)
	if err != nil {
		return err
	}
	fmt.Fprintf(out, "opened %s v%d: %s %s %s/%s over %s, n=%d, oracle %s, estimator %s\n", x.ID, x.Version, x.Assignment, x.Relation, x.Hypothesis.Item, x.Hypothesis.Revision, x.Population, x.N, x.Oracle, x.Analysis.Estimator)
	return nil
}

func experimentRun(args []string, j *journal.Journal, st *thought.Store, out, errw io.Writer) error {
	if len(args) < 1 {
		return fmt.Errorf("experiment run <exp> [--model m] [--judge-model m]")
	}
	exp := record.RecordID(args[0])
	model, judgeModel := "haiku", ""
	for i := 1; i < len(args); i++ {
		switch args[i] {
		case "--model":
			i++
			if i < len(args) {
				model = args[i]
			}
		case "--judge-model":
			i++
			if i < len(args) {
				judgeModel = args[i]
			}
		default:
			return fmt.Errorf("unknown flag %q", args[i])
		}
	}
	b, err := invoke.NewSubprocess(model)
	if err != nil {
		return err
	}
	var jb invoke.Backend
	if judgeModel != "" {
		if jb, err = invoke.NewSubprocess(judgeModel); err != nil {
			return err
		}
	}
	r := &experiment.Runner{J: j, Store: st, Backend: b, Judge: jb, Timeout: 20 * time.Minute, Events: func(e spine.Event) {
		fmt.Fprintf(errw, "event %s run=%s attempt=%d %s %s\n", e.Handle, e.Run, e.Attempt, e.Stage, e.Detail)
	}}
	if err := r.Run(context.Background(), exp); err != nil {
		return err
	}
	state, err := experiment.Fold(j, st)
	if err != nil {
		return err
	}
	n := 0
	for _, as := range state.Assignments {
		if as.Experiment == exp {
			n += len(state.Evidence[as.ID])
		}
	}
	fmt.Fprintf(out, "ran %s: %d/%d arm evidences\n", exp, n, 2*state.Experiments[exp].N)
	return nil
}
