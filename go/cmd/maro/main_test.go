package main

import (
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/closure"
)

// The goal-verdict line is the tranche's headline guarantee made
// operator-visible — it gets a pin, not just a comment (adversarial
// closure r2 2026-08-22, Skeptic: the r1 fix shipped untested).
func TestClosureLine(t *testing.T) {
	if got := closureLine(nil); got != "" {
		t.Fatalf("nil verdict must print nothing: %q", got)
	}
	judged := &closure.Verdict{Summary: "Achieved: all probes passed.",
		Confidence: 0.95, ChecksPassed: 4, ChecksRun: 4, Judged: true, Complete: true}
	got := closureLine(judged)
	if !strings.Contains(got, "Achieved: all probes passed.") ||
		!strings.Contains(got, "confidence 0.95") ||
		!strings.Contains(got, "4/4 checks passed") {
		t.Fatalf("judged line: %q", got)
	}
	skipped := &closure.Verdict{Summary: "Verification did not run.",
		SkipReason: "exception"}
	got = closureLine(skipped)
	if !strings.Contains(got, "[skipped: exception]") {
		t.Fatalf("skip path must name its reason: %q", got)
	}
	if strings.Contains(got, "checks passed") {
		t.Fatalf("skip line must not fake check counts: %q", got)
	}
}
