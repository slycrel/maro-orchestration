package main

import (
	"flag"
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/metrics"
)

// runMetrics is cli._cmd_metrics' default path: load the recent outcomes and
// print the human report.
//
// The `--format json` lane is NOT here. Python's is `json.dumps(asdict(
// metrics), indent=2)`, and asdict over SystemMetrics walks two nested
// dataclass maps whose KEYS can be non-strings — a shape json.dumps renders
// by coercing an int key to a string and refuses outright for a tuple. That
// is its own measurement job, and shipping a plausible-looking JSON lane
// before doing it is how a port acquires a divergence nobody has a fixture
// for. `-format json` is refused by name rather than silently ignored.
//
// The `pass-k` subcommand is also absent: compute_pass_at_k reads
// skill-stats.jsonl through skills.get_all_skill_stats, which lands with the
// remaining skills.py surface.
func runMetrics(args []string) error {
	fs := flag.NewFlagSet("metrics", flag.ContinueOnError)
	limit := fs.Int("limit", 100, "how many recent outcomes to read")
	format := fs.String("format", "text", "text (json is not ported yet)")
	// Same reason as the pack verbs: this command reads no positional, so a
	// bare Parse turned a stray word into a silent `-limit` drop.
	if _, err := parseArgs(fs, args, 0); err != nil {
		return err
	}
	if *format != "text" {
		return fmt.Errorf("maro metrics -format %s is not ported yet — "+
			"only -format text. The JSON lane is asdict() over a dataclass "+
			"tree with non-string dict keys and needs its own differential",
			*format)
	}
	m, err := metrics.GetMetrics(config.Workspace(), *limit)
	if err != nil {
		return err
	}
	report, err := metrics.FormatMetricsReport(m)
	if err != nil {
		return err
	}
	fmt.Println(report)
	return nil
}
