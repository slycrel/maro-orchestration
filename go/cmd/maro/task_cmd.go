package main

import (
	"fmt"
	"os"
	"sort"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/tasks"
)

// runTask is `python3 -m task_store` — the file-per-task queue's CLI.
//
// The one thing to notice here: this prints with ensure_ascii ON while the
// queue FILES are written with it off. That is not an inconsistency to fix
// — it is Python's, exactly: `_atomic_write` passes ensure_ascii=False and
// `main` calls a bare `json.dumps(task, indent=2)`, which defaults to
// True. So a reason field carrying "café" appears as raw UTF-8 in the file
// and as "café" on the terminal, in both runtimes.
func runTask(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: maro task enqueue|claim|complete|fail|list|status|archive|recover")
	}
	ws := config.Workspace()
	fmt.Fprintln(os.Stderr, "workspace:", ws)

	emit := func(v any) error {
		text, err := pyval.DumpsIndent2(v)
		if err != nil {
			return err
		}
		fmt.Println(text)
		return nil
	}

	switch args[0] {
	case "enqueue":
		fs, tf, arity := newTaskFlagSet("enqueue")
		lane, source, reason := tf.lane, tf.source, tf.reason
		parent, blockedBy := tf.parentJobID, tf.blockedBy
		if _, err := parseArgs(fs, args[1:], arity); err != nil {
			return err
		}
		var blocked []string
		for _, b := range strings.Split(*blockedBy, ",") {
			if b = strings.TrimSpace(b); b != "" {
				blocked = append(blocked, b)
			}
		}
		task, err := tasks.Enqueue(ws, tasks.Options{
			Lane: *lane, Source: *source, Reason: *reason,
			ParentJobID: *parent, BlockedBy: blocked,
		})
		if err != nil {
			return err
		}
		return emit(task)

	case "claim", "complete", "fail", "archive":
		fs, tf, arity := newTaskFlagSet(args[0])
		errText := tf.errText
		positional, err := parseArgs(fs, args[1:], arity)
		if err != nil {
			return err
		}
		if len(positional) < 1 {
			return fmt.Errorf("usage: maro task %s <job_id>", args[0])
		}
		jobID := positional[0]
		var task tasks.Task
		switch args[0] {
		case "claim":
			task, err = tasks.Claim(ws, jobID, 0)
		case "complete":
			task, err = tasks.Complete(ws, jobID, nil, "")
		case "fail":
			task, err = tasks.Fail(ws, jobID, *errText)
		case "archive":
			task, err = tasks.Archive(ws, jobID)
		}
		if err != nil {
			return err
		}
		return emit(task)

	case "list":
		fs, tf, arity := newTaskFlagSet("list")
		status := tf.status
		if _, err := parseArgs(fs, args[1:], arity); err != nil {
			return err
		}
		rows, err := tasks.List(ws, *status)
		if err != nil {
			return err
		}
		out := pyval.List{}
		for _, r := range rows {
			out = append(out, r)
		}
		return emit(out)

	case "status":
		// `status` and `recover` declare neither flags nor positionals, so
		// argparse rejects anything after the verb. These two took no
		// FlagSet at all and so silently ignored it.
		if err := refuseExtra(args[1:]); err != nil {
			return err
		}
		counts, err := tasks.StatusSummary(ws)
		if err != nil {
			return err
		}
		total := 0
		for _, f := range counts {
			total += f.Val.(int)
		}
		// Python emits `{"total": N, **counts}`, and counts is built by
		// iterating an UNSORTED glob — so its key order is filesystem
		// order and differs between two runs on the same box. There is no
		// order here to be faithful to, so this one sorts. Named rather
		// than silently matched, because "the orders differ" is a real
		// difference a byte comparison will show.
		//
		// SortStable, and over the fields rather than over a key set: two
		// distinct statuses can share a spelling, and both rows have to
		// survive the sort the way they survive json.dumps.
		sorted := append(pyval.Obj{}, counts...)
		sort.SliceStable(sorted, func(i, j int) bool {
			return sorted[i].Key < sorted[j].Key
		})
		out := pyval.Obj{{Key: "total", Val: total}}
		out = append(out, sorted...)
		return emit(out)

	case "recover":
		if err := refuseExtra(args[1:]); err != nil {
			return err
		}
		recovered, err := tasks.RecoverStaleClaims(ws)
		if err != nil {
			return err
		}
		ids := pyval.List{}
		for _, id := range recovered {
			ids = append(ids, id)
		}
		return emit(pyval.Obj{
			{Key: "recovered", Val: ids},
			{Key: "count", Val: len(recovered)},
		})
	}
	return fmt.Errorf("unknown task command %q", args[0])
}
