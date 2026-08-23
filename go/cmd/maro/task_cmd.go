package main

import (
	"flag"
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
		fs := flag.NewFlagSet("enqueue", flag.ContinueOnError)
		lane := fs.String("lane", "now", "queue lane")
		// The CLI's source default is "cli", not the library's
		// "task_store" — the difference is how a queue row says whether a
		// human or the runtime put it there.
		source := fs.String("source", "cli", "who enqueued this")
		reason := fs.String("reason", "", "why")
		parent := fs.String("parent-job-id", "", "parent job id")
		blockedBy := fs.String("blocked-by", "", "comma-separated job ids")
		if err := fs.Parse(args[1:]); err != nil {
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
		fs := flag.NewFlagSet(args[0], flag.ContinueOnError)
		errText := fs.String("error", "", "failure reason (fail only)")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if fs.NArg() < 1 {
			return fmt.Errorf("usage: maro task %s <job_id>", args[0])
		}
		jobID := fs.Arg(0)
		var task tasks.Task
		var err error
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
		fs := flag.NewFlagSet("list", flag.ContinueOnError)
		status := fs.String("status", "", "filter by status")
		if err := fs.Parse(args[1:]); err != nil {
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
		counts, err := tasks.StatusSummary(ws)
		if err != nil {
			return err
		}
		total := 0
		keys := make([]string, 0, len(counts))
		for k, v := range counts {
			total += v
			keys = append(keys, k)
		}
		// Python emits `{"total": N, **counts}`, and counts is built by
		// iterating an UNSORTED glob — so its key order is filesystem
		// order and differs between two runs on the same box. There is no
		// order here to be faithful to, so this one sorts. Named rather
		// than silently matched, because "the orders differ" is a real
		// difference a byte comparison will show.
		sort.Strings(keys)
		out := pyval.Obj{{Key: "total", Val: total}}
		for _, k := range keys {
			out = append(out, pyval.Field{Key: k, Val: counts[k]})
		}
		return emit(out)

	case "recover":
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
