package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	spine "github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// The operator-question lane (internal/run/ask.go): a worker that cannot
// proceed without the operator writes $MARO_ASK; the run ends on the
// question; `answer` commits the reply and runs the goal again after the
// asked run with the answer as operator context; `asks` lists the ledger.

// wireAsk names the ask file for a subprocess backend's workers: the
// workspace's drop directory, next to the derived-secrets drop.
func wireAsk(sp *invoke.Subprocess, a *workspace.Announced) string {
	p := filepath.Join(a.Path("drop"), spine.AskName)
	if err := os.MkdirAll(filepath.Dir(p), 0o700); err != nil {
		return ""
	}
	sp.Env = append(sp.Env, spine.AskEnv+"="+p)
	return p
}

// askedAttempt is the attempt of a run that asked: a committed Question,
// or the AGENDA clarity gate's unclear intent. Nil when the run asked
// nothing.
func askedAttempt(rs *spine.RunState) *spine.AttemptState {
	var asked *spine.AttemptState
	for _, a := range rs.Attempts {
		if a != nil && (a.Question != nil || (a.Intent != nil && !a.Intent.Clear)) {
			asked = a
		}
	}
	return asked
}

// cmdAnswer: answer <handle> [--source s] [--backend b] [--model m] <text…>.
// Commits the Answer against the asked run, then runs the goal again in
// its lane, lineage --after the asked run, with the answer as operator
// context. Refuses a run that asked nothing or is already answered.
func cmdAnswer(args []string, out, errw io.Writer) error {
	if len(args) < 2 {
		return fmt.Errorf("answer <handle> [--source s] [--backend b] [--model m] <text>")
	}
	handle := args[0]
	source := "cli"
	var passthrough, words []string
	for i := 1; i < len(args); i++ {
		switch args[i] {
		case "--source":
			if i+1 < len(args) {
				source = args[i+1]
				i++
			}
		case "--backend", "--model", "--judge-model", "--work":
			if i+1 < len(args) {
				passthrough = append(passthrough, args[i], args[i+1])
				i++
			}
		default:
			words = append(words, args[i])
		}
	}
	text := strings.TrimSpace(strings.Join(words, " "))
	if text == "" {
		return fmt.Errorf("answer: empty answer")
	}
	var lane spine.Lane
	var goal []byte
	var contextFile string
	err := withJournal(out, func(a *workspace.Announced, j *journal.Journal, st *thought.Store) error {
		led, err := spine.Fold(j.Production(), st)
		if err != nil {
			return err
		}
		var rs *spine.RunState
		for _, r := range led.Runs {
			if spine.HandleOf(r.Run) == handle {
				rs = r
			}
		}
		if rs == nil {
			return fmt.Errorf("answer: no run %s", handle)
		}
		asked := askedAttempt(rs)
		if asked == nil {
			return fmt.Errorf("answer: run %s asked nothing", handle)
		}
		if rs.Answer != nil {
			return fmt.Errorf("answer: run %s was already answered (%s): %s", handle, rs.Answer.Source, rs.Answer.Text)
		}
		var question string
		var qid record.RecordID
		late := false
		if q := asked.Question; q != nil {
			question, qid = q.Question, q.ID
			late = time.Now().After(q.Deadline)
		} else {
			question = asked.Intent.Question
		}
		ans := &spine.Answer{Header: record.Header{ID: record.NewID(), Schema: "answer/1", RunID: rs.Run, Attempt: asked.Attempt.Attempt, Subject: record.Ref{Kind: "run", ID: string(rs.Run)}, At: time.Now().UTC()},
			Target: rs.Run, Question: qid, Text: text, Source: source, Late: late}
		if _, err := j.Submit(context.Background(), journal.Command{IdempotencyKey: "answer/" + string(ans.ID), Epoch: j.Epoch(), Records: []record.Record{ans}}); err != nil {
			return err
		}
		goal, err = st.Get(rs.Goal.Text)
		if err != nil {
			return err
		}
		lane = rs.Goal.Lane
		contextFile = filepath.Join(a.Path("drop"), "answer-"+handle+".txt")
		if err := os.MkdirAll(filepath.Dir(contextFile), 0o700); err != nil {
			return err
		}
		if err := os.WriteFile(contextFile, spine.AnswerContext(question, text), 0o600); err != nil {
			return err
		}
		lateNote := ""
		if late {
			lateNote = " (late: past the question's time box)"
		}
		fmt.Fprintf(out, "answered %s%s: %s\n", handle, lateNote, question)
		return nil
	})
	if err != nil {
		return err
	}
	// the follow-up: the goal again, in its lane, after the asked run,
	// with the answer as operator context — the worker sees the question
	// and the answer and is told not to ask again
	nowArgs := append([]string{"--after", handle, "--context", contextFile}, passthrough...)
	return cmdNow(lane, append(nowArgs, string(goal)), out, errw)
}

// askRow is one line of `asks`: a question with its state.
type askRow struct {
	Handle      string    `json:"handle"`
	Status      string    `json:"status"` // pending | answered | expired
	Asked       time.Time `json:"asked"`
	Deadline    time.Time `json:"deadline"`
	Step        int       `json:"step,omitempty"`
	Question    string    `json:"question"`
	Why         string    `json:"why,omitempty"`
	Alternative string    `json:"no_input_alternative,omitempty"`
	Tried       bool      `json:"tried"`
	Answer      string    `json:"answer,omitempty"`
	Source      string    `json:"source,omitempty"`
	Late        bool      `json:"late,omitempty"`
}

// cmdAsks lists every operator question the workspace's runs asked, with
// its state — pending (waiting), answered (by whom), or expired (past its
// time box, still answerable).
func cmdAsks(args []string, out, errw io.Writer) error {
	asJSON := len(args) > 0 && args[0] == "--json"
	announce := out
	if asJSON {
		announce = errw // the workspace line must not break the JSON
	}
	return withJournal(announce, func(a *workspace.Announced, j *journal.Journal, st *thought.Store) error {
		led, err := spine.Fold(j.Production(), st)
		if err != nil {
			return err
		}
		var rows []askRow
		for _, rs := range led.Runs {
			for _, at := range rs.Attempts {
				if at == nil || at.Question == nil {
					continue
				}
				q := at.Question
				row := askRow{Handle: spine.HandleOf(rs.Run), Status: "pending", Asked: q.At, Deadline: q.Deadline, Step: q.Step, Question: q.Question, Why: q.Why, Alternative: q.NoInputAlternative, Tried: q.Tried}
				if ans := rs.Answer; ans != nil {
					row.Status, row.Answer, row.Source, row.Late = "answered", ans.Text, ans.Source, ans.Late
				} else if time.Now().After(q.Deadline) {
					row.Status = "expired"
				}
				rows = append(rows, row)
			}
		}
		sort.Slice(rows, func(i, k int) bool { return rows[i].Asked.Before(rows[k].Asked) })
		if asJSON {
			enc := json.NewEncoder(out)
			enc.SetIndent("", "  ")
			return enc.Encode(rows)
		}
		if len(rows) == 0 {
			fmt.Fprintln(out, "no questions asked")
			return nil
		}
		for _, r := range rows {
			mark := "?"
			switch r.Status {
			case "answered":
				mark = "✓"
			case "expired":
				mark = "×"
			}
			fmt.Fprintf(out, "%s %s %s asked %s until %s: %s\n", mark, r.Handle, r.Status, r.Asked.UTC().Format("2006-01-02 15:04Z"), r.Deadline.UTC().Format("2006-01-02 15:04Z"), r.Question)
			if r.Alternative != "" {
				fmt.Fprintf(out, "    tried without the operator (%v): %s\n", r.Tried, r.Alternative)
			}
			if r.Status == "answered" {
				late := ""
				if r.Late {
					late = ", late"
				}
				fmt.Fprintf(out, "    answer (%s%s): %s\n", r.Source, late, r.Answer)
			} else {
				fmt.Fprintf(out, "    answer with: maro-go answer %s \"<your answer>\"\n", r.Handle)
			}
		}
		return nil
	})
}
