// maro-go is the successor's binary. Step 1 exposes the workspace and the
// contracts foundation; the run driver arrives in later steps.
package main

import (
	"fmt"
	"os"

	"github.com/slycrel/maro-orchestration/go/internal/contracts"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "workspace":
		err = cmdWorkspace()
	case "contracts":
		err = cmdContracts(os.Args[2:])
	default:
		usage()
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "maro-go:", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: maro-go workspace | contracts gen|report|check [dir]")
}

func cmdWorkspace() error {
	r, err := workspace.Resolve()
	if err != nil {
		return err
	}
	r.Announce(os.Stdout)
	cur, live, err := workspace.Current(r)
	if err != nil {
		return err
	}
	switch {
	case cur == nil:
		fmt.Println("lease: none")
	case live:
		fmt.Printf("lease: held by pid %d epoch %d since %s\n", cur.PID, cur.Epoch, cur.Started)
	default:
		fmt.Printf("lease: STALE (pid %d dead) epoch %d\n", cur.PID, cur.Epoch)
	}
	return nil
}

func cmdContracts(args []string) error {
	if len(args) < 1 {
		usage()
		return fmt.Errorf("contracts needs a subcommand")
	}
	dir := contracts.Dir("contracts")
	if len(args) > 1 {
		dir = contracts.Dir(args[1])
	}
	gens := contracts.GenerateAll(contracts.SourceRef())
	switch args[0] {
	case "gen":
		if err := contracts.WriteGenerated(dir, gens); err != nil {
			return err
		}
		if err := contracts.WriteAnswerKey(dir); err != nil {
			return err
		}
		fmt.Printf("generated %d contracts + README.md + CENSUS.md into %s\n", len(gens), dir)
		return nil
	case "report":
		fs, err := contracts.Report(dir, gens)
		if err != nil {
			return err
		}
		fmt.Print(contracts.Render(fs))
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
			fmt.Println("DRIFT:", d)
		}
		if len(drift) > 0 {
			return fmt.Errorf("%d generated contract(s) drifted — regenerate and commit in the same change", len(drift))
		}
		fmt.Println("contracts: no drift")
		return nil
	}
	usage()
	return fmt.Errorf("unknown contracts subcommand %q", args[0])
}
