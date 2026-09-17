package run

import (
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
)

// A judge is shown the recorded execution next to the worker's claim: the
// step judge sees its step's effects, the closure judge sees every step's,
// a step that used no tools says so, and the fold re-derives every judge
// request from the same records — byte for byte.
func TestJudgesSeeTheRecordedEvidence(t *testing.T) {
	h := open(t)
	exec := scripted(outward,
		invoke.ScriptedCall{Response: []byte("Collected 12 rows"), Effects: []invoke.ScriptedEffect{
			{Op: "Bash", Input: []byte(`{"command":"wc -l data.csv"}`), Output: []byte("12 data.csv")},
		}},
		invoke.ScriptedCall{Response: []byte("Summary: revenue flat")},
	)
	judge := scripted(toolless,
		invoke.ScriptedCall{Response: []byte(intentClear)},
		invoke.ScriptedCall{Response: []byte(planTwo)},
		invoke.ScriptedCall{Response: []byte(judgeDone)},
		invoke.ScriptedCall{Response: []byte(judgeDone)},
		invoke.ScriptedCall{Response: []byte(closureYes)},
	)
	d := h.agenda(exec, judge)
	if _, err := d.Run(ctxBg, []byte("Collect the numbers, then summarize them."), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	if len(judge.Seen) != 5 {
		t.Fatalf("judge calls: %d", len(judge.Seen))
	}
	step1, step2, closure := string(judge.Seen[2].Prompt), string(judge.Seen[3].Prompt), string(judge.Seen[4].Prompt)
	for _, want := range []string{"### evidence\n", "tools: offered\n", "#0 Bash [", ": ok\n  | 12 data.csv\n"} {
		if !strings.Contains(step1, want) {
			t.Fatalf("step 1's judge lacks %q:\n%s", want, step1)
		}
	}
	if !strings.Contains(step2, "### evidence\n") || !strings.Contains(step2, "no recorded effects") || strings.Contains(step2, "12 data.csv") {
		t.Fatalf("step 2's judge evidence is not its own:\n%s", step2)
	}
	for _, want := range []string{"### evidence 1\n", "  | 12 data.csv\n", "### evidence 2\n", "no recorded effects"} {
		if !strings.Contains(closure, want) {
			t.Fatalf("closure judge lacks %q:\n%s", want, closure)
		}
	}
	// the fold re-derives every judge request with the same evidence
	if _, err := Fold(h.j.Production(), h.st); err != nil {
		t.Fatalf("fold: %v", err)
	}
}

// A NOW run's closure judge sees its one call's evidence too, and a
// tool-less call says so rather than reading as "nothing happened". A NOW
// run asks a model closure judge only when ModelJudge is set; which
// backend answers is the policy's MechModelJudge boundary, so both the
// run's backend and the bound judge carry the closure answer and the
// prompt is found on whichever was asked.
func TestNowClosureSeesTheRecordedEvidence(t *testing.T) {
	closurePrompt := func(t *testing.T, backends ...*invoke.Scripted) string {
		t.Helper()
		for _, b := range backends {
			for _, r := range b.Seen {
				if strings.Contains(string(r.Prompt), "### evidence 1\n") {
					return string(r.Prompt)
				}
			}
		}
		t.Fatal("no closure judge request carried an evidence section")
		return ""
	}
	h := open(t)
	b := scripted(outward,
		invoke.ScriptedCall{Response: []byte("wrote it"), Effects: []invoke.ScriptedEffect{
			{Op: "Write", Input: []byte(`{"path":"out.md"}`), Output: []byte("ok")},
		}},
		invoke.ScriptedCall{Response: []byte(closureYes)},
	)
	judge := scripted(toolless, invoke.ScriptedCall{Response: []byte(closureYes)})
	d := h.driver(b, nil)
	d.ModelJudge, d.Judge = true, judge
	if _, err := d.Run(ctxBg, []byte("Write the file"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	closure := closurePrompt(t, b, judge)
	for _, want := range []string{"tools: offered\n", "#0 Write [", "  | ok\n"} {
		if !strings.Contains(closure, want) {
			t.Fatalf("NOW closure judge lacks %q:\n%s", want, closure)
		}
	}
	if _, err := Fold(h.j.Production(), h.st); err != nil {
		t.Fatalf("fold: %v", err)
	}

	// tool-less: the section is present and says no effects were possible
	h2 := open(t)
	b2 := scripted(toolless, invoke.ScriptedCall{Response: []byte("Paris")}, invoke.ScriptedCall{Response: []byte(closureYes)})
	judge2 := scripted(toolless, invoke.ScriptedCall{Response: []byte(closureYes)})
	d2 := h2.driver(b2, nil)
	d2.ModelJudge, d2.Judge = true, judge2
	if _, err := d2.Run(ctxBg, []byte("Capital of France?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	if p := closurePrompt(t, b2, judge2); !strings.Contains(p, "tools: not offered") {
		t.Fatalf("tool-less NOW closure judge:\n%s", p)
	}
}
