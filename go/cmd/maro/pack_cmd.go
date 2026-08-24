// maro pack — the portable-learning lifecycle (export → human review →
// seal → import → adopt), cross-runtime compatible with Python's
// maro-pack. Every subcommand prints the resolved workspace before any
// write (live-store discipline).
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/pack"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

func runPack(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: maro pack export|seal|import|adopt [flags]")
	}
	switch args[0] {
	case "export":
		return packExport(args[1:])
	case "seal":
		return packSeal(args[1:])
	case "import":
		return packImport(args[1:])
	case "adopt":
		return packAdopt(args[1:])
	default:
		return fmt.Errorf("unknown pack subcommand %q (export|seal|import|adopt)", args[0])
	}
}

func resolveWorkspace(flagValue string) (string, error) {
	ws := flagValue
	if ws == "" {
		ws = config.Workspace()
	}
	abs, err := filepath.Abs(ws)
	if err != nil {
		return "", err
	}
	// Assert the resolved store BEFORE any write — the resolved path is
	// part of the result (live-store discipline, 2026-08-16 incident).
	fmt.Printf("workspace: %s\n", abs)
	return abs, nil
}

func printJSON(v any) error {
	// json.dumps(..., indent=2): the Python CLI's shape, for whatever
	// parses this (mission-r8).
	//
	// printJSON's callers pass report STRUCTS (usually by pointer), so the
	// widening has to pick an arm. pyval.FromStruct deliberately refuses a
	// non-struct rather than guessing — that refusal is what kept the
	// map[string]int hole from spreading — so the guess is made HERE, once,
	// where the two possible shapes are both known and local.
	widened, err := pyval.FromStruct(v)
	if err != nil {
		out, ferr := pyval.DumpsIndent2(pyval.FromPlain(v))
		if ferr != nil {
			return ferr
		}
		fmt.Println(out)
		return nil
	}
	out, err := pyval.DumpsIndent2(widened)
	if err != nil {
		return err
	}
	fmt.Println(out)
	return nil
}

func packExport(args []string) error {
	fs := flag.NewFlagSet("pack export", flag.ContinueOnError)
	name := fs.String("name", "", "pack name (required)")
	label := fs.String("label", "", "origin label (required)")
	workspace := fs.String("workspace", "", "source workspace (default: resolved)")
	outDir := fs.String("out", "", "output dir (default: <ws>/output/packs)")
	includeMedium := fs.Bool("include-medium", false, "also export medium-tier lessons")
	includeKnowledge := fs.Bool("include-knowledge", false, "also export knowledge web")
	includePlaybook := fs.Bool("include-playbook", false, "also export playbook.md")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *name == "" || *label == "" {
		return fmt.Errorf("pack export: -name and -label are required")
	}
	ws, err := resolveWorkspace(*workspace)
	if err != nil {
		return err
	}
	res, err := pack.Export(pack.ExportOpts{
		Name: *name, Label: *label, Workspace: ws, OutDir: *outDir,
		IncludeMedium: *includeMedium, IncludeKnowledge: *includeKnowledge,
		IncludePlaybook: *includePlaybook,
	})
	if err != nil {
		return err
	}
	fmt.Printf("pack: %s (UNSEALED)\nreview: %s\n", res.PackPath, res.ReviewPath)
	fmt.Println("read the review, then: maro pack seal -pack", res.PackPath, "--yes")
	return nil
}

func packSeal(args []string) error {
	fs := flag.NewFlagSet("pack seal", flag.ContinueOnError)
	packPath := fs.String("pack", "", "path to the .maropack.tar.gz (required)")
	yes := fs.Bool("yes", false, "confirm the REVIEW.md was read by a human")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *packPath == "" {
		return fmt.Errorf("pack seal: -pack is required")
	}
	manifest, err := pack.Seal(*packPath, *yes)
	if err != nil {
		return err
	}
	review := manifest["review"].(map[string]any)
	fmt.Printf("sealed: %s\nreviewed_at: %v\npayload_sha256: %v\n",
		*packPath, review["reviewed_at"], review["review_payload_sha256"])
	return nil
}

func packImport(args []string) error {
	fs := flag.NewFlagSet("pack import", flag.ContinueOnError)
	packPath := fs.String("pack", "", "path to the .maropack.tar.gz (required)")
	label := fs.String("label", "", "provenance label (required)")
	target := fs.String("target", "", "target workspace (default: resolved)")
	allowUnreviewed := fs.Bool("allow-unreviewed", false,
		"accept an unsealed pack (self-to-self transfers only)")
	dryRun := fs.Bool("dry-run", false, "report without writing")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *packPath == "" || *label == "" {
		return fmt.Errorf("pack import: -pack and -label are required")
	}
	ws, err := resolveWorkspace(*target)
	if err != nil {
		return err
	}
	report, err := pack.Import(pack.ImportOpts{
		PackPath: *packPath, Label: *label, Target: ws,
		AllowUnreviewed: *allowUnreviewed, DryRun: *dryRun,
	})
	if err != nil {
		return err
	}
	return printJSON(report)
}

func packAdopt(args []string) error {
	fs := flag.NewFlagSet("pack adopt", flag.ContinueOnError)
	label := fs.String("label", "", "import label to adopt from (required)")
	target := fs.String("target", "", "target workspace (default: resolved)")
	all := fs.Bool("all", false, "adopt everything quarantined under the label")
	dryRun := fs.Bool("dry-run", false, "report without writing")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *label == "" {
		return fmt.Errorf("pack adopt: -label is required")
	}
	ws, err := resolveWorkspace(*target)
	if err != nil {
		return err
	}
	report, err := pack.Adopt(pack.AdoptOpts{
		Label: *label, Target: ws, Items: fs.Args(), All: *all, DryRun: *dryRun,
	})
	if err != nil {
		return err
	}
	if len(report.Adopted) == 0 && len(report.Skipped) == 0 {
		fmt.Fprintln(os.Stderr, "nothing selected")
	}
	return printJSON(report)
}
