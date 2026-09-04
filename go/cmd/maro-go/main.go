// maro-go is the successor's binary. Step 1 exposes the workspace and the
// contracts foundation; the run driver arrives in later steps.
package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/slycrel/maro-orchestration/go/internal/contracts"
	_ "github.com/slycrel/maro-orchestration/go/internal/invoke" // registers the invocation kinds
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/projector"
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
	fmt.Fprintln(w, "usage: maro-go workspace | contracts gen|report|check [dir] | journal status|publish")
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
		p, err := projector.New(j, projector.ThoughtsView{})
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
