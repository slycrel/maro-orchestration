// maro-go is the successor's binary: workspace, contracts, journal, and the
// NOW driver (one goal in, one delivered answer out).
package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/contracts"
	"github.com/slycrel/maro-orchestration/go/internal/experiment"
	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/process"
	"github.com/slycrel/maro-orchestration/go/internal/projector"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	spine "github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/tail"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	_ "github.com/slycrel/maro-orchestration/go/internal/verdict" // registers the judging kinds
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

// run is the testable entry: args in, exit code out, everything written to
// the given writers. Exit 2 = usage, 1 = failure, 0 = ok.
func run(args []string, stdout, stderr io.Writer) int {
	if len(args) < 1 {
		usage(stderr)
		return 2
	}
	var err error
	switch args[0] {
	case "workspace":
		err = cmdWorkspace(stdout)
	case "contracts":
		err = cmdContracts(args[1:], stdout, stderr)
	case "journal":
		err = cmdJournal(args[1:], stdout)
	case "now":
		err = cmdNow(spine.LaneNow, args[1:], stdout, stderr)
	case "agenda":
		err = cmdNow(spine.LaneAgenda, args[1:], stdout, stderr)
	case "ack":
		err = cmdAck(args[1:], stdout)
	case "runs":
		err = cmdRuns(args[1:], stdout, stderr)
	case "learn":
		err = cmdLearn(args[1:], stdout)
	case "experiment":
		err = cmdExperiment(args[1:], stdout, stderr)
	case "serve":
		err = cmdServe(args[1:], stdout, stderr)
	case "submit":
		err = cmdSubmit(args[1:], stdout, stderr)
	case "interrupt":
		err = cmdInterrupt(args[1:], stdout)
	case "status":
		err = cmdStatus(stdout)
	default:
		usage(stderr)
		return 2
	}
	if err != nil {
		fmt.Fprintln(stderr, "maro-go:", err)
		return 1
	}
	return 0
}

func usage(w io.Writer) {
	fmt.Fprintln(w, "usage: maro-go workspace | contracts gen|report|check [dir] | journal status|publish | now|agenda [--backend b] [--model m] [--judge-model m] [--ack] <goal> | ack <delivery> <token> | runs [resume|show <handle>] | learn add|stage|list | experiment open [--live --population f --n k]|run|close [--judge-model m]|list|show | serve [--model m] [--judge-model m] | submit [--lane now|agenda] [--ack] <goal> | interrupt <handle> --why <text> | status")
}

func cmdWorkspace(out io.Writer) error {
	r, err := workspace.Resolve()
	if err != nil {
		return err
	}
	a, err := r.Announce(out)
	if err != nil {
		return err
	}
	if err := a.Ensure(); err != nil {
		return err
	}
	cur, live, err := workspace.Status(a)
	if err != nil {
		return err
	}
	switch {
	case cur == nil && !live:
		fmt.Fprintln(out, "lease: none")
	case cur == nil && live:
		fmt.Fprintln(out, "lease: held (lease.json unreadable)")
	case live:
		fmt.Fprintf(out, "lease: held by pid %d epoch %d on %s since %s\n", cur.PID, cur.Epoch, cur.Host, cur.Started)
	default:
		fmt.Fprintf(out, "lease: STALE (pid %d, lock free) epoch %d\n", cur.PID, cur.Epoch)
	}
	return nil
}

// cmdJournal opens the workspace under the lease, reports the journal's
// state, and (publish) runs the projector once. It holds the lease only for
// the duration of the command.
func cmdJournal(args []string, out io.Writer) error {
	if len(args) < 1 {
		return fmt.Errorf("journal needs status|publish")
	}
	r, err := workspace.Resolve()
	if err != nil {
		return err
	}
	a, err := r.Announce(out)
	if err != nil {
		return err
	}
	if err := a.Ensure(); err != nil {
		return err
	}
	l, err := workspace.Acquire(a)
	if err != nil {
		return err
	}
	defer l.Release()
	j, err := journal.Open(l)
	if err != nil {
		return err
	}
	defer j.Close()
	rec := j.Recovered()
	pub, err := projector.Published(a)
	if err != nil {
		return err
	}
	fmt.Fprintf(out, "journal: head=%d frames=%d epoch=%d published=%d\n", rec.Head, rec.Frames, j.Epoch(), pub)
	if rec.Discarded > 0 {
		fmt.Fprintf(out, "journal: RECOVERED — discarded %d bytes of short tail\n", rec.Discarded)
	}
	switch args[0] {
	case "status":
		return nil
	case "publish":
		st, err := thought.Open(a)
		if err != nil {
			return err
		}
		p, err := projector.New(j, projector.ThoughtsView{}, spine.OutcomesView{Store: st})
		if err != nil {
			return err
		}
		w, err := p.Publish()
		if err != nil {
			return err
		}
		fmt.Fprintf(out, "published: %d → %s\n", w, projector.Current(a))
		return nil
	}
	return fmt.Errorf("unknown journal subcommand %q", args[0])
}

func cmdContracts(args []string, out, errw io.Writer) error {
	if len(args) < 1 {
		usage(errw)
		return fmt.Errorf("contracts needs a subcommand")
	}
	dir := contracts.Dir("contracts")
	if len(args) > 1 {
		dir = contracts.Dir(args[1])
	}
	repoRoot, _ := filepath.Abs(filepath.Join(string(dir), ".."))
	gens := contracts.GenerateAll(contracts.SourceRef())
	switch args[0] {
	case "gen":
		if err := contracts.WriteGenerated(dir, gens); err != nil {
			return err
		}
		if err := contracts.WriteAnswerKey(dir); err != nil {
			return err
		}
		fmt.Fprintf(out, "generated %d contracts + MANIFEST.json + README.md + CENSUS.md into %s\n", len(gens), dir)
		return nil
	case "report":
		fs, err := contracts.Report(dir, gens, repoRoot)
		if err != nil {
			return err
		}
		fmt.Fprint(out, contracts.Render(fs))
		drift, _ := contracts.Drift(dir, gens)
		fmt.Fprint(out, contracts.Insufficiency(dir, gens, fs, drift))
		fmt.Fprintf(out, "report: %d error(s), %d warning(s)\n", len(contracts.Errors(fs)), len(contracts.Warnings(fs)))
		if e := contracts.Errors(fs); len(e) > 0 {
			return fmt.Errorf("%d contract error(s)", len(e))
		}
		return nil
	case "check":
		drift, err := contracts.Drift(dir, gens)
		if err != nil {
			return err
		}
		for _, d := range drift {
			fmt.Fprintln(out, "DRIFT:", d)
		}
		if len(drift) > 0 {
			return fmt.Errorf("%d generated contract(s) drifted — regenerate and commit in the same change", len(drift))
		}
		fmt.Fprintln(out, "contracts: no drift")
		return nil
	}
	usage(errw)
	return fmt.Errorf("unknown contracts subcommand %q", args[0])
}

// cmdNow takes one goal from the CLI origin through the NOW configuration
// of the driver, in-process, holding the lease for the run. Flags:
//
//	--backend scripted|subprocess (default subprocess)   --model <name> (default haiku)
//	--ack   (policy: user_acknowledged; the presentation prints the ack command)
//
// The always-on submission path (a Goal written into the running process
// via the lease's socket) arrives with the supervisor (step 7).
func cmdNow(lane spine.Lane, args []string, out, errw io.Writer) error {
	var text []string
	backend, model, judgeModel, policy := "subprocess", "haiku", "", spine.DeliveryPolicy{Required: spine.TransportAccepted}
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--backend":
			i++
			if i < len(args) {
				backend = args[i]
			}
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
		case "--ack":
			policy.Required = spine.UserAcknowledged
		default:
			text = append(text, args[i])
		}
	}
	goal := strings.TrimSpace(strings.Join(text, " "))
	if goal == "" {
		return fmt.Errorf("now needs a goal: maro-go now [--backend subprocess|scripted] [--model m] [--ack] <goal text>")
	}
	var b, jb invoke.Backend
	switch backend {
	case "subprocess":
		s, err := invoke.NewSubprocess(model)
		if err != nil {
			return err
		}
		b = s
		if judgeModel != "" {
			js, err := invoke.NewSubprocess(judgeModel)
			if err != nil {
				return err
			}
			jb = js
		}
	case "scripted":
		b = &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted", Model: "scripted"}, Calls: []invoke.ScriptedCall{{Response: []byte("scripted response to: " + goal)}}}
	default:
		return fmt.Errorf("unknown backend %q (subprocess|scripted)", backend)
	}
	return withJournal(out, func(j *journal.Journal, st *thought.Store) error {
		d := &spine.Driver{J: j, Store: st, Backend: b, Judge: jb, Lane: lane, ModelJudge: jb != nil, Origin: spine.CLIOrigin{W: out}, Timeout: 20 * time.Minute, Admit: experiment.Admit(j, st),
			Events: func(e spine.Event) {
				fmt.Fprintf(errw, "event %s run=%s attempt=%d %s %s\n", e.Handle, e.Run, e.Attempt, e.Stage, e.Detail)
			}}
		rep, err := d.Run(context.Background(), []byte(goal), policy)
		if err != nil {
			return err
		}
		fmt.Fprintf(errw, "mission: %s (execution %s, closure %s, delivery %s)\n", rep.Mission.Outcome, rep.Mission.Terminal, rep.Mission.Closure, rep.Mission.Delivery)
		// the tail, in-process: one pass over what is recorded (the always-on
		// process has a lane for it)
		lens := jb
		if lens == nil {
			lens = b
		}
		tl := &tail.Tail{J: j, Store: st, Lens: lens, Timeout: 5 * time.Minute, Events: func(l string) { fmt.Fprintln(errw, l) }}
		if _, err := tl.Pass(context.Background()); err != nil {
			fmt.Fprintln(errw, "tail:", err)
		}
		return nil
	})
}

// cmdAck records a client-generated acknowledgement: maro-go ack <delivery-id> <token>.
func cmdAck(args []string, out io.Writer) error {
	if len(args) != 2 {
		return fmt.Errorf("ack needs <delivery-id> <token>")
	}
	// a running process holds the lease: acknowledge through it
	var dialErr error
	if sock, err := socketPath(io.Discard); err == nil {
		cl, err := process.Dial(sock)
		dialErr = err
		if err == nil {
			defer cl.Close()
			ev, err := cl.One(process.Request{Op: "ack", Delivery: args[0], Token: args[1]})
			if err != nil {
				return err
			}
			if ev.Replayed {
				fmt.Fprintf(out, "acknowledged (already, delivery %s)\n", ev.Delivery)
			} else {
				fmt.Fprintf(out, "acknowledged: delivery %s\n", ev.Delivery)
			}
			return nil
		}
	}
	err := withJournal(out, func(j *journal.Journal, st *thought.Store) error {
		ack, replayed, err := spine.Ack(context.Background(), j, st, record.RecordID(args[0]), args[1])
		if err != nil {
			return err
		}
		if replayed {
			fmt.Fprintf(out, "acknowledged (already, %s)\n", ack.ID)
		} else {
			fmt.Fprintf(out, "acknowledged: %s\n", ack.ID)
		}
		return nil
	})
	if errors.Is(err, workspace.ErrLeaseHeld) {
		return fmt.Errorf("%w — a process holds the workspace but its socket did not answer (%v); if it is `maro-go serve`, its intake lane is down: check `maro-go status` or restart it; if it is an in-process run, the token stays valid, retry once it exits", err, dialErr)
	}
	return err
}

// cmdRuns lists every run's mission fold; `resume` first finishes what a
// previous process left non-terminal (reconcile → recover → deliver).
func cmdRuns(args []string, out, errw io.Writer) error {
	return withJournal(out, func(j *journal.Journal, st *thought.Store) error {
		if len(args) > 1 && args[0] == "show" {
			led, err := spine.Fold(j.Production(), st)
			if err != nil {
				return err
			}
			payload, m, err := spine.LatestPayload(led, st, args[1])
			if err != nil {
				return err
			}
			out.Write(payload)
			fmt.Fprintf(out, "\n---\nrun %s attempt %d · %s · closure %s · delivery %s/%s\n", m.Handle, m.Attempt, m.Outcome, m.Closure, m.Delivery, m.Required)
			return nil
		}
		if len(args) > 0 && args[0] == "resume" {
			d := &spine.Driver{J: j, Store: st, Backend: &invoke.Scripted{Caps: invoke.Capabilities{Name: "resume-only", Model: "none"}}, Origin: spine.CLIOrigin{W: out}}
			s, err := invoke.NewSubprocess("haiku")
			if err == nil {
				d.Backend = s
			} else {
				fmt.Fprintln(errw, "resume: no subprocess backend available; runs needing re-execution will fail honestly:", err)
			}
			reps, err := d.Resume(context.Background())
			for _, r := range reps {
				fmt.Fprintf(errw, "resumed %s attempt %d: %s\n", r.Handle, r.Attempt, r.Mission.Outcome)
			}
			if err != nil {
				return err
			}
		}
		led, err := spine.Fold(j.Production(), st)
		if err != nil {
			return err
		}
		for _, g := range led.Unstarted {
			fmt.Fprintf(out, "goal %s: taken in, run not started\n", g.ID)
		}
		var rows []spine.Mission
		for _, rs := range led.Runs {
			rows = append(rows, spine.MissionOf(rs))
		}
		sort.Slice(rows, func(i, k int) bool { return rows[i].Run < rows[k].Run })
		for _, m := range rows {
			dup := ""
			if m.MayDuplicate > 0 {
				dup = fmt.Sprintf(" may_duplicate=%d", m.MayDuplicate)
			}
			if m.Stuck != "" {
				dup += " STUCK(" + m.Stuck + ")"
			}
			fmt.Fprintf(out, "%s attempt %d  %-24s execution=%s terminal=%s closure=%s delivery=%s/%s%s\n", m.Handle, m.Attempt, m.Outcome, m.Execution, m.Terminal, m.Closure, m.Delivery, m.Required, dup)
		}
		return nil
	})
}

// withJournal announces the workspace, takes the lease, opens the journal
// and thought store, runs fn, and releases — one command, one lease hold.
func withJournal(out io.Writer, fn func(*journal.Journal, *thought.Store) error) error {
	r, err := workspace.Resolve()
	if err != nil {
		return err
	}
	a, err := r.Announce(out)
	if err != nil {
		return err
	}
	if err := a.Ensure(); err != nil {
		return err
	}
	l, err := workspace.Acquire(a)
	if err != nil {
		return err
	}
	defer l.Release()
	j, err := journal.Open(l)
	if err != nil {
		return err
	}
	defer j.Close()
	st, err := thought.Open(a)
	if err != nil {
		return err
	}
	return fn(j, st)
}

// cmdLearn is the operator's memory surface (§7, v1 producer of learned
// revisions and lifecycle transitions):
//
//	learn add [--scope workspace|goal:<id>] [--family f] <text>   → a lesson at candidate
//	learn stage <item> <to> --why <text>                          → an operator transition of the CURRENT revision
//	learn list                                                     → items, current revision, standing
func cmdLearn(args []string, out io.Writer) error {
	if len(args) < 1 {
		return fmt.Errorf("learn needs add|stage|list")
	}
	return withJournal(out, func(j *journal.Journal, st *thought.Store) error {
		if _, err := learn.EnsureSeeds(context.Background(), j); err != nil {
			return err
		}
		head := j.Head() // the fold below reads exactly this much; a transition is submitted against it
		led, err := learn.Fold(j.Production())
		if err != nil {
			return err
		}
		switch args[0] {
		case "add":
			scope, family, policy := learn.ScopeWorkspace, "", ""
			var text []string
			for i := 1; i < len(args); i++ {
				switch args[i] {
				case "--policy":
					i++
					if i < len(args) {
						policy = args[i]
					}
				case "--scope":
					i++
					if i < len(args) {
						scope = learn.ScopePath(args[i])
					}
				case "--family":
					i++
					if i < len(args) {
						family = args[i]
					}
				default:
					text = append(text, args[i])
				}
			}
			item := learn.LearnedID(record.NewID())
			r := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: time.Now().UTC()},
				Item: item, LearnedKind: learn.Lesson, Scope: scope, Family: family, Provenance: learn.Provenance{Source: "operator", Why: "maro-go learn add"}}
			body := strings.TrimSpace(strings.Join(text, " "))
			switch {
			case policy != "":
				// a policy is data, not text: --policy <mechanism>=on|off
				mech, state, ok := strings.Cut(policy, "=")
				if !ok || (state != "on" && state != "off") || body != "" {
					return fmt.Errorf("learn add --policy <mechanism>=on|off (no text)")
				}
				r.LearnedKind, r.Policy = learn.Policy, &learn.PolicyRule{Mechanism: learn.Mechanism(mech), Enabled: state == "on"}
			case body == "":
				return fmt.Errorf("learn add needs the lesson text (or --policy)")
			default:
				ref, err := st.Put(thought.LessonText, []byte(body))
				if err != nil {
					return err
				}
				r.Text = ref
			}
			if _, err := j.Submit(context.Background(), journal.Command{IdempotencyKey: "learn/add/" + string(item), Epoch: j.Epoch(), Records: []record.Record{r}}); err != nil {
				return err
			}
			fmt.Fprintf(out, "added %s %s revision %s at candidate (scope %s)\n", r.LearnedKind, item, r.ID, scope)
			return nil
		case "stage":
			if len(args) < 3 {
				return fmt.Errorf("learn stage <item> <to> --why <text>")
			}
			item, to, why := learn.LearnedID(args[1]), learn.Stage(args[2]), ""
			for i := 3; i < len(args); i++ {
				if args[i] == "--why" && i+1 < len(args) {
					why = args[i+1]
				}
			}
			if why == "" {
				return fmt.Errorf("learn stage needs --why")
			}
			it := led.Items[item]
			if it == nil {
				return fmt.Errorf("no item %s", item)
			}
			from := it.StageOf(it.Current.ID)
			x := &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: time.Now().UTC()},
				Item: item, Revision: it.Current.ID, From: from, To: to, Actor: learn.ActorOperator, Why: why}
			if _, err := j.Submit(context.Background(), journal.Command{IdempotencyKey: "learn/stage/" + string(x.ID), Epoch: j.Epoch(), ExpectHead: &head, Records: []record.Record{x}}); err != nil {
				return err
			}
			fmt.Fprintf(out, "%s revision %s: %s → %s\n", item, it.Current.ID, from, to)
			return nil
		case "list":
			ids := make([]string, 0, len(led.Items))
			for id := range led.Items {
				ids = append(ids, string(id))
			}
			sort.Strings(ids)
			for _, id := range ids {
				it := led.Items[learn.LearnedID(id)]
				var body []byte
				if it.Current.LearnedKind == learn.Policy {
					body = []byte(fmt.Sprintf("%s=%v", it.Current.Policy.Mechanism, it.Current.Policy.Enabled))
				} else {
					body, _ = st.Get(it.Current.Text)
				}
				fmt.Fprintf(out, "%s  rev %d/%s  %-11s  %-9s src=%-8s scope=%s family=%q  %s\n", id, len(it.Revisions), it.Current.ID, it.StageOf(it.Current.ID), it.Current.LearnedKind, it.Current.Provenance.Source, it.Current.Scope, it.Current.Family, string(body))
			}
			return nil
		}
		return fmt.Errorf("unknown learn subcommand %q", args[0])
	})
}

// cmdServe is the always-on process: lease, journal, supervisor, lanes,
// socket. It runs until SIGINT/SIGTERM, then quiesces in stage order.
func cmdServe(args []string, out, errw io.Writer) error {
	model, judgeModel := "haiku", ""
	for i := 0; i < len(args); i++ {
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
		}
	}
	r, err := workspace.Resolve()
	if err != nil {
		return err
	}
	a, err := r.Announce(out)
	if err != nil {
		return err
	}
	b, err := invoke.NewSubprocess(model)
	if err != nil {
		return err
	}
	var jb invoke.Backend
	if judgeModel != "" {
		js, err := invoke.NewSubprocess(judgeModel)
		if err != nil {
			return err
		}
		jb = js
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	srv, err := process.Serve(context.Background(), process.Options{Root: a, Backend: b, Judge: jb, Timeout: 20 * time.Minute, Log: errw})
	if err != nil {
		return err
	}
	fmt.Fprintf(out, "serving on %s\n", srv.Socket())
	<-ctx.Done()
	fmt.Fprintln(errw, "quiescing…")
	stopCtx, stopCancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer stopCancel()
	return srv.Stop(stopCtx)
}

// socketPath is the workspace's socket, announced.
func socketPath(out io.Writer) (string, error) {
	r, err := workspace.Resolve()
	if err != nil {
		return "", err
	}
	a, err := r.Announce(out)
	if err != nil {
		return "", err
	}
	return a.Path(process.SocketName), nil
}

// cmdSubmit sends a goal to the running process and prints what comes back.
func cmdSubmit(args []string, out, errw io.Writer) error {
	req := process.Request{Lane: string(spine.LaneNow)}
	var text []string
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--lane":
			i++
			if i < len(args) {
				req.Lane = args[i]
			}
		case "--ack":
			req.Ack = true
		default:
			text = append(text, args[i])
		}
	}
	req.Text = strings.TrimSpace(strings.Join(text, " "))
	if req.Text == "" {
		return fmt.Errorf("submit needs a goal: maro-go submit [--lane now|agenda] [--ack] <goal text>")
	}
	sock, err := socketPath(out)
	if err != nil {
		return err
	}
	cl, err := process.Dial(sock)
	if err != nil {
		return fmt.Errorf("%w — start one with `maro-go serve`, or use `now`/`agenda` in-process", err)
	}
	defer cl.Close()
	return cl.Submit(context.Background(), req, func(ev process.Event) {
		switch ev.Type {
		case "accepted":
			fmt.Fprintf(errw, "accepted goal %s\n", ev.Goal)
		case "presentation":
			fmt.Fprint(out, ev.Payload)
			fmt.Fprintf(out, "\n---\nrun %s · terminal %s · closure %s · delivery %s\n", ev.Handle, ev.Terminal, ev.Closure, ev.Delivery)
			if ev.MayDuplicate > 0 {
				fmt.Fprintf(out, "(re-presented: %d earlier presentation(s) ended with the process dying)\n", ev.MayDuplicate)
			}
			for _, h := range ev.Health {
				fmt.Fprintf(out, "degraded: %s\n", h)
			}
			if ev.Token != "" {
				fmt.Fprintf(out, "acknowledge with: maro-go ack %s %s\n", ev.Delivery, ev.Token)
			}
		case "done":
			fmt.Fprintf(errw, "mission: %s (execution %s, closure %s)\n", ev.Mission, ev.Terminal, ev.Closure)
		}
	})
}

// cmdInterrupt asks the running process to stop a run at its next boundary.
func cmdInterrupt(args []string, out io.Writer) error {
	if len(args) < 1 {
		return fmt.Errorf("interrupt <handle> --why <text>")
	}
	why := ""
	for i := 1; i < len(args); i++ {
		if args[i] == "--why" && i+1 < len(args) {
			why = args[i+1]
		}
	}
	if strings.TrimSpace(why) == "" {
		return fmt.Errorf("interrupt needs --why")
	}
	sock, err := socketPath(out)
	if err != nil {
		return err
	}
	cl, err := process.Dial(sock)
	if err != nil {
		return err
	}
	defer cl.Close()
	ev, err := cl.One(process.Request{Op: "interrupt", Handle: args[0], Why: why})
	if err != nil {
		return err
	}
	fmt.Fprintf(out, "interrupt %s: %s\n", ev.Handle, ev.Result)
	return nil
}

// cmdStatus prints the running process's lanes and runs.
func cmdStatus(out io.Writer) error {
	sock, err := socketPath(out)
	if err != nil {
		return err
	}
	cl, err := process.Dial(sock)
	if err != nil {
		return err
	}
	defer cl.Close()
	ev, err := cl.One(process.Request{Op: "status"})
	if err != nil {
		return err
	}
	for _, l := range ev.Lanes {
		state := "up"
		switch {
		case l.GaveUp:
			state = "DOWN"
		case !l.Up:
			state = "down"
		case l.Stalled:
			state = "stalled"
		}
		fmt.Fprintf(out, "lane %-10s gen=%d %-7s watermark=%d %s\n", l.Lane, l.Generation, state, l.Watermark, l.Reason)
	}
	for _, m := range ev.Runs {
		fmt.Fprintf(out, "%s attempt %d  %-24s execution=%s terminal=%s closure=%s delivery=%s/%s\n", m.Handle, m.Attempt, m.Outcome, m.Execution, m.Terminal, m.Closure, m.Delivery, m.Required)
	}
	return nil
}
