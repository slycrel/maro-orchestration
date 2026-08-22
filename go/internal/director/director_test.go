package director

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/workers"
)

func readLog(t *testing.T, path string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	return m
}

// TestRunDryEndToEnd: the dry path is deterministic top to bottom —
// one inferred ticket, stub worker, verbatim-concat report, durable
// log with report_echoed left null (nothing to judge on the concat
// path — an echo check there could not fail).
func TestRunDryEndToEnd(t *testing.T) {
	ws := t.TempDir()
	rec := record.New(ws)
	res, err := Run(context.Background(), nil, rec, "summarize the release notes", true)
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" || len(res.Tickets) != 1 || len(res.WorkerResults) != 1 {
		t.Fatalf("dry run must produce one stub ticket+worker: %+v", res)
	}
	if res.TokensIn != 60 || res.TokensOut != 40 {
		t.Fatalf("dry usage must flow up from the stub worker: %d/%d", res.TokensIn, res.TokensOut)
	}
	if !strings.Contains(res.Report, "[dry-run:") {
		t.Fatalf("dry report must be the verbatim concat: %q", res.Report)
	}
	if res.LogPath == "" {
		t.Fatalf("dry run must still write the director log")
	}
	logRow := readLog(t, res.LogPath)
	wr := logRow["worker_results"].([]any)[0].(map[string]any)
	if v, present := wr["report_echoed"]; !present || v != nil {
		t.Fatalf("concat-path echo must be null (not judged), got %v", v)
	}
}

// scriptFor builds the Fake script for a 1-ticket LLM run:
// spec → worker → review [...extra rounds] → compile.
func fakeRun(t *testing.T, ws string, script []string, directive string) (Result, *llm.Fake) {
	t.Helper()
	fake := &llm.Fake{Script: script}
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, directive, false)
	if err != nil {
		t.Fatal(err)
	}
	return res, fake
}

// TestRunLLMPathEchoStamped: an accepted worker whose distinctive terms
// reach the compiled report stamps ReportEchoed=true; token totals
// carry every call (spec+worker+review+compile = 4 Fake calls).
func TestRunLLMPathEchoStamped(t *testing.T) {
	ws := t.TempDir()
	res, fake := fakeRun(t, ws, []string{
		`{"spec": "one pass", "tickets": [{"worker_type": "research", "task": "inspect the flux capacitor readings"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "flux capacitor readings show tachyon overflow at threshold-9931", "summary": "s"}`,
		`{"accepted": true, "reason": "complete"}`,
		"Report: the flux capacitor readings revealed tachyon overflow at threshold-9931.",
	}, "check the machine")
	if res.Status != "done" {
		t.Fatalf("run must be done: %+v", res)
	}
	// challenger is call 2 in Python order — here the spec parse
	// succeeded, so calls are spec, challenge, worker, review, compile.
	if got := len(fake.Prompts); got != 5 {
		t.Fatalf("expected 5 LLM calls (spec, challenge, worker, review, compile), got %d", got)
	}
	if res.TokensIn != 5*10 || res.TokensOut != 5*5 {
		t.Fatalf("every call's usage must accumulate: %d/%d", res.TokensIn, res.TokensOut)
	}
	w := res.WorkerResults[0]
	if w.ReportEchoed == nil || !*w.ReportEchoed {
		t.Fatalf("echoed worker must stamp true: %+v", w.ReportEchoed)
	}
}

// TestRunReportOmissionEventAndFalseStamp: a DONE worker whose content
// makes no lexical contact with the compiled report stamps false AND
// emits the WORKER_REPORT_OMISSION candidate event (MH #6) — the
// advisory record that the subagent's work was dropped.
func TestRunReportOmissionEventAndFalseStamp(t *testing.T) {
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "one pass", "tickets": [{"worker_type": "research", "task": "inspect the flux capacitor readings"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "quixotic zymurgy handbook describes phlogiston bottling procedures thoroughly", "summary": "s"}`,
		`{"accepted": true, "reason": "complete"}`,
		"Nothing to report.",
	}, "check the machine")
	w := res.WorkerResults[0]
	if w.ReportEchoed == nil || *w.ReportEchoed {
		t.Fatalf("dropped worker must stamp false: %+v", w.ReportEchoed)
	}
	events, err := os.ReadFile(filepath.Join(ws, "memory", "captains_log.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(events), "WORKER_REPORT_OMISSION") {
		t.Fatalf("omission candidate event must be durable: %s", events)
	}
	// The log row must carry the false stamp too.
	logRow := readLog(t, res.LogPath)
	wr := logRow["worker_results"].([]any)[0].(map[string]any)
	if v, ok := wr["report_echoed"].(bool); !ok || v {
		t.Fatalf("log row must carry report_echoed=false: %v", wr)
	}
}

// TestRunDelegationGapEventScopedToWorker: a worker-authored
// provision-shaped block emits WORKER_DELEGATION_GAP; the run goes
// stuck.
func TestRunDelegationGapEventScopedToWorker(t *testing.T) {
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "one pass", "tickets": [{"worker_type": "ops", "task": "restart the service"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "flag_blocked", "reason": "the service name was not provided"}`,
		`{"accepted": false, "reason": "blocked", "revision_request": null}`,
		"Report: nothing completed.",
	}, "restart it")
	if res.Status != "stuck" {
		t.Fatalf("blocked worker must leave the run stuck: %+v", res.Status)
	}
	events, _ := os.ReadFile(filepath.Join(ws, "memory", "captains_log.jsonl"))
	if !strings.Contains(string(events), "WORKER_DELEGATION_GAP") {
		t.Fatalf("delegation-gap candidate must be durable: %s", events)
	}
	logRow := readLog(t, res.LogPath)
	wr := logRow["worker_results"].([]any)[0].(map[string]any)
	if v, ok := wr["delegation_gap"].(bool); !ok || !v {
		t.Fatalf("log row must carry delegation_gap=true: %v", wr)
	}
}

// TestRunRevisionRoundOnRejection: a rejected review with a revision
// request re-dispatches once (maxReviewRounds=2) and the audit trail
// keeps BOTH decisions.
func TestRunRevisionRoundOnRejection(t *testing.T) {
	ws := t.TempDir()
	res, fake := fakeRun(t, ws, []string{
		`{"spec": "one pass", "tickets": [{"worker_type": "build", "task": "write the widget"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "half a widget only, needs more work clearly", "summary": "s"}`,
		`{"accepted": false, "reason": "incomplete", "revision_request": "finish the other half"}`,
		`{"tool": "deliver_result", "result": "the complete widget with both halves attached", "summary": "s"}`,
		`{"accepted": true, "reason": "complete now"}`,
		"Report: the complete widget with both halves attached is delivered.",
	}, "make a widget")
	if res.Status != "done" {
		t.Fatalf("revision acceptance must land done: %+v", res)
	}
	if len(res.ReviewDecisions) != 2 {
		t.Fatalf("audit trail must keep both decisions: %+v", res.ReviewDecisions)
	}
	if len(res.WorkerResults) != 1 || !strings.Contains(res.WorkerResults[0].Result, "both halves") {
		t.Fatalf("the revised result must replace the rejected one: %+v", res.WorkerResults)
	}
	// The revision prompt must carry the revision request to the worker.
	joined := strings.Join(fake.Prompts, "\n<CALL>\n")
	if !strings.Contains(joined, "Revision request: finish the other half") {
		t.Fatalf("revision request must reach the re-dispatched worker")
	}
	// Correlation must RESOLVE in the persisted log (adversarial
	// director r2, both lenses' HIGH: the revised decision's TicketID
	// was an orphaned key — present in review_decisions, absent from
	// tickets). Every decision's ticket_id must match a persisted
	// ticket row, and the revised row must carry revision_of.
	logRow := readLog(t, res.LogPath)
	known := map[string]bool{}
	revisionOf := ""
	for _, ti := range logRow["tickets"].([]any) {
		row := ti.(map[string]any)
		known[row["ticket_id"].(string)] = true
		if v, ok := row["revision_of"].(string); ok {
			revisionOf = v
		}
	}
	if len(known) != 2 {
		t.Fatalf("revised ticket must be persisted beside the original: %v", logRow["tickets"])
	}
	if revisionOf != res.Tickets[0].TicketID {
		t.Fatalf("revision chain must resolve to the original ticket: %q", revisionOf)
	}
	for _, di := range logRow["review_decisions"].([]any) {
		row := di.(map[string]any)
		if !known[row["ticket_id"].(string)] {
			t.Fatalf("orphaned review-decision key %v not in tickets[]", row["ticket_id"])
		}
	}
}

// TestReviewParseFailureRejects: an unparseable review verdict REJECTS
// — auto-accepting hides bad output (the safety direction).
func TestReviewParseFailureRejects(t *testing.T) {
	fake := &llm.Fake{Script: []string{"not json at all"}}
	d, _, _ := reviewWorkerOutput(context.Background(), fake, "dir", Ticket{WorkerType: "build", Task: "t"},
		workers.Result{Status: "done", Result: "some output"}, false)
	if d.Accepted {
		t.Fatalf("parse failure must reject: %+v", d)
	}
	if !strings.Contains(d.Reason, "rejecting for safety") {
		t.Fatalf("rejection must name itself: %+v", d)
	}
}

// TestSpecParseFailureFallsBackToSingleTicket: a director that can't
// plan still delegates the whole directive to one inferred worker.
func TestSpecParseFailureFallsBackToSingleTicket(t *testing.T) {
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		"the spec reply is prose, not JSON",
		// challenger still runs, on the fallback spec (Python parity);
		// empty revised_spec keeps it. Then worker, review, compile.
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "did the whole directive in one go anyway", "summary": "s"}`,
		`{"accepted": true, "reason": "fine"}`,
		"Report: did the whole directive in one go anyway, complete.",
	}, "analyze the flurbo market")
	if len(res.Tickets) != 1 {
		t.Fatalf("spec parse failure must fall back to a single ticket: %+v", res.Tickets)
	}
	if res.Tickets[0].Task != "analyze the flurbo market" {
		t.Fatalf("fallback ticket carries the whole directive: %+v", res.Tickets[0])
	}
	if res.Tickets[0].WorkerType != "research" {
		t.Fatalf("fallback worker type is inferred: %+v", res.Tickets[0])
	}
}

// TestWriteLogScrubsProse: directive/spec/task prose is scrubbed at the
// single durable write boundary.
func TestWriteLogScrubsProse(t *testing.T) {
	ws := t.TempDir()
	rec := record.New(ws)
	res := Result{
		DirectorID: "testscrub",
		Directive:  "rotate key AKIAIOSFODNN7EXAMPLE now",
		Spec:       "use AKIAIOSFODNN7EXAMPLE for the rotation",
		Tickets:    []Ticket{{TicketID: "t1", WorkerType: "ops", Task: "apply AKIAIOSFODNN7EXAMPLE"}},
	}
	path, err := writeLog(rec, res)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(path)
	if strings.Contains(string(b), "AKIAIOSFODNN7EXAMPLE") {
		t.Fatalf("secret survived the log write boundary: %s", b)
	}
}

func TestEstimateGoalScope(t *testing.T) {
	cases := map[string]string{
		"what is the config value":                        "narrow",
		"adversarial review of the entire codebase":       "wide",
		"build a complete orchestration platform":         "deep",
		"implement retry logic for the fetcher subsystem": "medium",
	}
	for goal, want := range cases {
		if got := EstimateGoalScope(goal); got != want {
			t.Fatalf("EstimateGoalScope(%q) = %q, want %q", goal, got, want)
		}
	}
	if !isLargeScopeReview("full audit of the repo") {
		t.Fatalf("wide goals are large-scope")
	}
}

func TestRequiresExplicitAcceptance(t *testing.T) {
	if !RequiresExplicitAcceptance("deploy the new build") {
		t.Fatalf("deploy must trigger explicit acceptance")
	}
	if RequiresExplicitAcceptance("summarize the readme") {
		t.Fatalf("read-only directives are inferred")
	}
}

// TestReportEchoTriState: nil (nothing to judge) vs false (dropped) vs
// true (contact) — consumers must keep nil distinct from false.
func TestReportEchoTriState(t *testing.T) {
	if got := reportEcho("", "a report"); got != nil {
		t.Fatalf("empty result must be unjudged")
	}
	if got := reportEcho("tiny", "a report"); got != nil {
		t.Fatalf("too few distinctive terms must be unjudged")
	}
	res := "quixotic zymurgy phlogiston bottling procedures thoroughly"
	if got := reportEcho(res, "totally unrelated text here"); got == nil || *got {
		t.Fatalf("no contact must be false, got %v", got)
	}
	if got := reportEcho(res, "the zymurgy handbook covers phlogiston bottling"); got == nil || !*got {
		t.Fatalf("3-term contact must be true, got %v", got)
	}
}

// noAccessAdapter fails every call with a provision-SHAPED error
// message — the exact corpus-contamination case the origin scoping
// exists for ("LLM call failed: no access…" is an infrastructure
// failure, not an under-specified ticket).
type noAccessAdapter struct{}

func (noAccessAdapter) Name() string { return "noaccess" }
func (noAccessAdapter) Complete(context.Context, []llm.Message, llm.Options) (*llm.Response, error) {
	return nil, &llm.ResultError{Msg: "no access to endpoint"}
}

// TestRunDelegationGapNotEmittedForAdapterBlocks: an adapter-blocked
// worker whose reason pattern-matches the provision keywords must NOT
// emit WORKER_DELEGATION_GAP — only worker-authored reasons count
// (2026-08-11 review; the log row's delegation_gap is false too).
func TestRunDelegationGapNotEmittedForAdapterBlocks(t *testing.T) {
	ws := t.TempDir()
	rec := record.New(ws)
	res, err := Run(context.Background(), noAccessAdapter{}, rec, "restart the flurbulator", false)
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "stuck" || res.WorkerResults[0].BlockedOrigin != "adapter" {
		t.Fatalf("all-failing adapter must leave a stuck run with adapter-blocked worker: %+v", res.WorkerResults)
	}
	if !workers.DelegationGap(res.WorkerResults[0].StuckReason) {
		t.Fatalf("fixture must be provision-shaped or the pin is vacuous: %q", res.WorkerResults[0].StuckReason)
	}
	events, rerr := os.ReadFile(filepath.Join(ws, "memory", "captains_log.jsonl"))
	if rerr == nil && strings.Contains(string(events), "WORKER_DELEGATION_GAP") {
		t.Fatalf("adapter-authored reason must not contaminate the candidate corpus: %s", events)
	}
	logRow := readLog(t, res.LogPath)
	wr := logRow["worker_results"].([]any)[0].(map[string]any)
	if v, _ := wr["delegation_gap"].(bool); v {
		t.Fatalf("log row must scope delegation_gap to worker-authored blocks: %v", wr)
	}
}

// TestRunGapEventScrubbed: the delegation-gap event's ticket/reason
// previews land in captains_log.jsonl SCRUBBED — the sibling director
// log already scrubbed this exact string and the event sink didn't
// (adversarial director r1, three lenses; Python's log_event doesn't
// scrub — Go-stricter, backport candidate).
func TestRunGapEventScrubbed(t *testing.T) {
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "one pass", "tickets": [{"worker_type": "ops", "task": "rotate key AKIAIOSFODNN7EXAMPLE now"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "flag_blocked", "reason": "the key AKIAIOSFODNN7EXAMPLE rotation target was not provided"}`,
		`{"accepted": false, "reason": "blocked", "revision_request": null}`,
		"Report: nothing completed.",
	}, "rotate the key")
	if res.Status != "stuck" {
		t.Fatalf("fixture must block: %+v", res.Status)
	}
	events, err := os.ReadFile(filepath.Join(ws, "memory", "captains_log.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(events), "WORKER_DELEGATION_GAP") {
		t.Fatalf("gap event must still fire: %s", events)
	}
	if strings.Contains(string(events), "AKIAIOSFODNN7EXAMPLE") {
		t.Fatalf("secret survived into captains_log: %s", events)
	}
}

// TestReviewVerdictFieldGate: a parseable verdict whose "accepted" is
// missing, null, or mistyped REJECTS — the safety direction applies to
// the field, not just the envelope (adversarial director r1, three
// lenses; deliberately stricter than Python's data.get("accepted",
// True) which accepts an absent key and coerces "false" truthy).
func TestReviewVerdictFieldGate(t *testing.T) {
	for _, reply := range []string{
		`{"reason": "looks fine"}`,
		`{"accepted": null, "reason": "cannot decide"}`,
		`{"accepted": "false", "reason": "string-typed"}`,
		`{"accepted": 1, "reason": "number-typed"}`,
	} {
		fake := &llm.Fake{Script: []string{reply}}
		d, _, _ := reviewWorkerOutput(context.Background(), fake, "dir",
			Ticket{TicketID: "tk1", WorkerType: "build", Task: "t"},
			workers.Result{Status: "done", Result: "some output"}, false)
		if d.Accepted {
			t.Fatalf("verdict %q must reject, got accept", reply)
		}
		if d.TicketID != "tk1" {
			t.Fatalf("decision must carry its ticket id: %+v", d)
		}
	}
	// A well-formed false still parses as an ordinary rejection.
	fake := &llm.Fake{Script: []string{`{"accepted": false, "reason": "incomplete"}`}}
	d, _, _ := reviewWorkerOutput(context.Background(), fake, "dir",
		Ticket{TicketID: "tk2", WorkerType: "build", Task: "t"},
		workers.Result{Status: "done", Result: "some output"}, false)
	if d.Accepted || d.Reason != "incomplete" {
		t.Fatalf("well-formed false must reject with its own reason: %+v", d)
	}
}

// TestReviewVerdictGateKeepsDiagnostics: a mistyped "accepted" beside a
// REAL reason and revision_request rejects WITHOUT discarding either —
// the guidance still drives a revision round and the audit trail names
// the malformed type (adversarial director r2, Skeptic HIGH: the r1
// gate converted a revisable rejection into an unrevisable best-effort
// ship).
func TestReviewVerdictGateKeepsDiagnostics(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		`{"accepted": "false", "reason": "missing the config section", "revision_request": "add the config section"}`,
	}}
	d, _, _ := reviewWorkerOutput(context.Background(), fake, "dir",
		Ticket{TicketID: "tk3", WorkerType: "build", Task: "t"},
		workers.Result{Status: "done", Result: "some output"}, false)
	if d.Accepted {
		t.Fatalf("mistyped accepted must reject: %+v", d)
	}
	if d.RevisionRequest != "add the config section" {
		t.Fatalf("model's revision guidance must survive the gate: %+v", d)
	}
	if !strings.Contains(d.Reason, "string") || !strings.Contains(d.Reason, "missing the config section") {
		t.Fatalf("reason must name the malformed type AND keep the model's diagnostics: %q", d.Reason)
	}
}

// TestReviewWhitespaceRevisionTrimmed: a whitespace-only
// revision_request is EMPTY guidance — it must not dodge the
// no-revision warning or buy a vacuous retry (adversarial director r2,
// QA).
func TestReviewWhitespaceRevisionTrimmed(t *testing.T) {
	fake := &llm.Fake{Script: []string{`{"accepted": false, "reason": "bad", "revision_request": "   "}`}}
	d, _, _ := reviewWorkerOutput(context.Background(), fake, "dir",
		Ticket{TicketID: "tk4", WorkerType: "build", Task: "t"},
		workers.Result{Status: "done", Result: "some output"}, false)
	if d.RevisionRequest != "" {
		t.Fatalf("whitespace guidance must trim to empty: %q", d.RevisionRequest)
	}
}

// TestSpecMalformedWarningsBounded: N malformed spec-ticket entries
// produce ONE summary warning naming the count, not N warnings — the
// reject path is bounded like the accept path (adversarial director
// r2, Skeptic MED).
func TestSpecMalformedWarningsBounded(t *testing.T) {
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "one pass", "tickets": [{"task": 1}, {"task": 2}, {"task": 3}, {"worker_type": "build", "task": "the real task to build"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "built the real task completely as asked", "summary": "s"}`,
		`{"accepted": true, "reason": "fine"}`,
		"Report: built the real task completely as asked, done.",
	}, "build the things")
	count := 0
	for _, w := range res.Warnings {
		if strings.Contains(w, "task) skipped") {
			count++
			if !strings.Contains(w, "3 malformed spec ticket entries") {
				t.Fatalf("summary must carry the count: %q", w)
			}
		}
	}
	if count != 1 {
		t.Fatalf("exactly one summary warning, got %d: %+v", count, res.Warnings)
	}
}

// TestRunPersistsDecisionsAndWarnings: the durable log carries the
// review audit trail (ticket-correlated) and the run's warnings — a
// rejected-no-revision run must not read as an unqualified success
// once stderr is gone (adversarial director r1, QA HIGHs).
func TestRunPersistsDecisionsAndWarnings(t *testing.T) {
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "one pass", "tickets": [{"worker_type": "build", "task": "write the widget"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "half a widget only, not finished at all", "summary": "s"}`,
		`{"accepted": false, "reason": "incomplete", "revision_request": null}`,
		"Report: half a widget only.",
	}, "make a widget")
	found := false
	for _, w := range res.Warnings {
		if strings.Contains(w, "rejected with no revision request") {
			found = true
		}
	}
	if !found {
		t.Fatalf("silent-rejection warning must fire: %+v", res.Warnings)
	}
	logRow := readLog(t, res.LogPath)
	drows, ok := logRow["review_decisions"].([]any)
	if !ok || len(drows) != 1 {
		t.Fatalf("review audit trail must be durable: %v", logRow["review_decisions"])
	}
	d0 := drows[0].(map[string]any)
	if d0["accepted"] != false || d0["ticket_id"] != res.Tickets[0].TicketID {
		t.Fatalf("decision row must be ticket-correlated and honest: %v", d0)
	}
	warns, ok := logRow["warnings"].([]any)
	if !ok || len(warns) == 0 {
		t.Fatalf("warnings must be durable: %v", logRow["warnings"])
	}
}

// TestRunMalformedTicketEntriesSkipped: a forged/malformed ticket
// entry (non-string task) is skipped with a warning, never dispatched
// as an EMPTY ticket; all-malformed falls back to the single whole-
// directive ticket (adversarial director r1, three lenses).
func TestRunMalformedTicketEntriesSkipped(t *testing.T) {
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "one pass", "tickets": [{"worker_type": "build", "task": 42}, {"worker_type": "build", "task": "the real task to build"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "built the real task completely as asked", "summary": "s"}`,
		`{"accepted": true, "reason": "fine"}`,
		"Report: built the real task completely as asked, done.",
	}, "build the things")
	if len(res.Tickets) != 1 || res.Tickets[0].Task != "the real task to build" {
		t.Fatalf("malformed entry must be skipped, real one kept: %+v", res.Tickets)
	}
	warned := false
	for _, w := range res.Warnings {
		if strings.Contains(w, "task) skipped") {
			warned = true
		}
	}
	if !warned {
		t.Fatalf("skip must be named: %+v", res.Warnings)
	}

	ws2 := t.TempDir()
	res2, _ := fakeRun(t, ws2, []string{
		`{"spec": "one pass", "tickets": [{"worker_type": "build", "task": null}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "did the whole directive in one pass anyway", "summary": "s"}`,
		`{"accepted": true, "reason": "fine"}`,
		"Report: did the whole directive in one pass anyway, complete.",
	}, "build the flurbo assembly")
	if len(res2.Tickets) != 1 || res2.Tickets[0].Task != "build the flurbo assembly" {
		t.Fatalf("all-malformed must fall back to the whole directive: %+v", res2.Tickets)
	}
}

// TestCompileEchoJudgesClippedWindow: the echo check judges against
// the SAME clipped window the compiler saw — distinctive terms living
// past the cut must not produce a false DROPPED verdict (adversarial
// director r1, Skeptic MED; Python compares the unclipped text — named
// divergence, honesty-direction). Here the visible window has too few
// distinctive terms to judge, so the stamp is nil, not false.
func TestCompileEchoJudgesClippedWindow(t *testing.T) {
	filler := strings.Repeat("ab ", 1400) // >4000 chars, zero distinctive terms
	tail := "zephyrquark phlogiston zymurgy bottling procedures thoroughly"
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "one pass", "tickets": [{"worker_type": "research", "task": "inspect the archive"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": ` + string(mustJSON(t, filler+tail)) + `, "summary": "s"}`,
		`{"accepted": true, "reason": "fine"}`,
		"Report: nothing relevant surfaced.",
	}, "check the archive")
	w := res.WorkerResults[0]
	if w.ReportEchoed != nil {
		t.Fatalf("terms past the compile window must not judge (nil, not %v)", *w.ReportEchoed)
	}
	events, rerr := os.ReadFile(filepath.Join(ws, "memory", "captains_log.jsonl"))
	if rerr == nil && strings.Contains(string(events), "WORKER_REPORT_OMISSION") {
		t.Fatalf("no false DROPPED accusation for unseen content: %s", events)
	}
}

func mustJSON(t *testing.T, s string) []byte {
	t.Helper()
	b, err := json.Marshal(s)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// TestReportEchoIgnoresClipMarkerVocab: clip-marker vocabulary is
// framework text, not worker content — a clipped 4-term window must
// stay below the judgeable floor instead of borrowing "truncated"/
// "characters" to cross it and then matching the report's own
// truncation notice (adversarial director r2, Skeptic; mutation M53
// was undetected until this pin).
func TestReportEchoIgnoresClipMarkerVocab(t *testing.T) {
	window := "alphaterm betaterm gammaterm deltaterm … [truncated: first 10 of 20 characters]"
	report := "report mentions alphaterm and was truncated to fewer characters"
	if got := reportEcho(window, report); got != nil {
		t.Fatalf("marker vocab must not make a 4-term window judgeable: got %v", *got)
	}
}

// TestRunRevisionRejectedNoGuidanceSingleWarning: a revision round that
// rejects WITHOUT new guidance is the terminal round — it gets the
// exhaustion warning and ONLY that (adversarial director r3, Skeptic
// HIGH: the r2 mid-loop guard fired on the sole iteration and doubled
// up with the exhaustion warning, while its comment claimed the branch
// was unreachable).
func TestRunRevisionRejectedNoGuidanceSingleWarning(t *testing.T) {
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "one pass", "tickets": [{"worker_type": "build", "task": "write the widget"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "half a widget only, needs more work clearly", "summary": "s"}`,
		`{"accepted": false, "reason": "incomplete", "revision_request": "finish the other half"}`,
		`{"tool": "deliver_result", "result": "still only half a widget after the revision", "summary": "s"}`,
		`{"accepted": false, "reason": "still incomplete"}`,
		"Report: best effort widget.",
	}, "make a widget")
	exhausted, stopping := 0, 0
	for _, w := range res.Warnings {
		if strings.Contains(w, "review exhausted") {
			exhausted++
		}
		if strings.Contains(w, "stopping revisions") {
			stopping++
		}
	}
	if exhausted != 1 || stopping != 0 {
		t.Fatalf("one incident must write one warning (exhausted=%d stopping=%d): %+v",
			exhausted, stopping, res.Warnings)
	}
}

// TestSpecNonObjectEntriesCounted: non-object ticket entries (strings,
// numbers, null) are the most LLM-plausible schema confusion and must
// hit the malformed counter like bad-task objects do (adversarial
// director r3, both lenses: they were evading it entirely).
func TestSpecNonObjectEntriesCounted(t *testing.T) {
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "one pass", "tickets": [null, 7, "do the api", {"task": 1}, {"worker_type": "build", "task": "the real task to build"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "built the real task completely as asked", "summary": "s"}`,
		`{"accepted": true, "reason": "fine"}`,
		"Report: built the real task completely as asked, done.",
	}, "build the things")
	found := false
	for _, w := range res.Warnings {
		if strings.Contains(w, "4 malformed spec ticket entries") {
			found = true
		}
	}
	if !found {
		t.Fatalf("non-object entries must be counted alongside bad-task objects: %+v", res.Warnings)
	}
	if len(res.Tickets) != 1 || res.Tickets[0].Task != "the real task to build" {
		t.Fatalf("the one valid ticket must survive: %+v", res.Tickets)
	}
}

// TestSpecTicketsFieldNotAListWarns: a present-but-non-list tickets
// field discards the model's whole plan — the single-ticket fallback
// must fire ON THE RECORD (adversarial director r3, QA HIGH: it fell
// through with zero durable trace).
func TestSpecTicketsFieldNotAListWarns(t *testing.T) {
	for _, tickets := range []string{`"build it then test it"`, `42`, `true`, `{"a": 1}`, `null`} {
		t.Run(tickets, func(t *testing.T) {
			ws := t.TempDir()
			res, _ := fakeRun(t, ws, []string{
				`{"spec": "one pass", "tickets": ` + tickets + `}`,
				`{"critiques": [], "revised_spec": ""}`,
				`{"tool": "deliver_result", "result": "did the directive end to end as requested", "summary": "s"}`,
				`{"accepted": true, "reason": "fine"}`,
				"Report: did the directive end to end as requested.",
			}, "build the things")
			found := false
			for _, w := range res.Warnings {
				if strings.Contains(w, "not a list") {
					found = true
				}
			}
			if !found {
				t.Fatalf("non-list tickets %s must warn: %+v", tickets, res.Warnings)
			}
			if len(res.Tickets) != 1 || res.Tickets[0].Task != "build the things" {
				t.Fatalf("single-ticket fallback must carry the directive: %+v", res.Tickets)
			}
		})
	}
}

// TestReportEchoMarkerStripScoped: budget.Clip's own marker at the
// true end of the window is stripped — offsets included — while
// GENUINE content that merely discusses truncation keeps its
// vocabulary (adversarial director r3, both lenses: the r2
// word-deletes were unconditional and partial; re-scoped r5).
func TestReportEchoMarkerStripScoped(t *testing.T) {
	// (a) big offsets: digits must not become distinctive terms.
	window := "alphaterm betaterm gammaterm deltaterm … [truncated: first 50000 of 123456 characters]"
	if got := reportEcho(window, "report echoing alphaterm 50000 123456 truncated"); got != nil {
		t.Fatalf("marker offsets must not make a 4-term window judgeable: %v", *got)
	}
	// (b) The Accumulator's "entry" wording never reaches this path as
	// framework text (reportEcho only sees budget.Clip output), so an
	// entry-shaped marker here is worker-authored CONTENT and keeps
	// its vocabulary (adversarial director r5, QA: r3/r4 stripped it
	// on a wrong call-path assumption).
	window = "alphaterm betaterm gammaterm deltaterm … [entry truncated: first 10 of 20 characters]"
	if got := reportEcho(window, "report"); got == nil || *got {
		t.Fatalf("entry-marker content must stay judgeable (7 terms, no echo): %v", got)
	}
	// (c) genuine unclipped content KEEPS its truncation vocabulary.
	window = "payload truncated deliberately characters exceeded limits"
	report := "the payload was truncated as characters exceeded the cap"
	got := reportEcho(window, report)
	if got == nil || !*got {
		t.Fatalf("genuine truncation vocabulary must stay judgeable: %v", got)
	}
}

// TestRunRevisionCorrelationMultiTicket: with two tickets where only
// the FIRST is revised, revision_of must resolve to the correct
// sibling and the untouched ticket's row must carry no revision_of
// (adversarial director r3, Skeptic: the single-ticket pin could not
// distinguish ordering bugs across the shared res.Tickets slice).
func TestRunRevisionCorrelationMultiTicket(t *testing.T) {
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "two passes", "tickets": [{"worker_type": "build", "task": "write the widget"}, {"worker_type": "research", "task": "survey the widget market"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "half a widget only, needs more work clearly", "summary": "s"}`,
		`{"accepted": false, "reason": "incomplete", "revision_request": "finish the other half"}`,
		`{"tool": "deliver_result", "result": "the complete widget with both halves attached", "summary": "s"}`,
		`{"accepted": true, "reason": "complete now"}`,
		`{"tool": "deliver_result", "result": "the widget market survey covers all vendors", "summary": "s"}`,
		`{"accepted": true, "reason": "thorough"}`,
		"Report: widget complete and market surveyed across all vendors.",
	}, "widget program")
	logRow := readLog(t, res.LogPath)
	rows := logRow["tickets"].([]any)
	if len(rows) != 3 {
		t.Fatalf("two originals + one revision must persist: %v", rows)
	}
	var originalIDs []string
	for _, ti := range rows {
		row := ti.(map[string]any)
		if _, revised := row["revision_of"]; !revised {
			originalIDs = append(originalIDs, row["ticket_id"].(string))
		}
	}
	if len(originalIDs) != 2 {
		t.Fatalf("exactly the two originals must lack revision_of: %v", rows)
	}
	for _, ti := range rows {
		row := ti.(map[string]any)
		if v, ok := row["revision_of"].(string); ok && v != originalIDs[0] {
			t.Fatalf("revision_of must point at the FIRST original %q, got %q", originalIDs[0], v)
		}
	}
}

// TestRunRevisionThreeRoundsSingleWarning: the one-incident-one-warning
// invariant must hold at maxReviewRounds>2, not just at the shipped
// constant — a mid-loop no-guidance rejection warns once and the
// exhaustion warning stays silent (adversarial director r4, QA HIGH:
// r3's arithmetic gate was true only at N=2; a constant bump silently
// reintroduced the double warning).
func TestRunRevisionThreeRoundsSingleWarning(t *testing.T) {
	orig := maxReviewRounds
	maxReviewRounds = 3
	t.Cleanup(func() { maxReviewRounds = orig })
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "one pass", "tickets": [{"worker_type": "build", "task": "write the widget"}]}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "half a widget only, needs more work clearly", "summary": "s"}`,
		`{"accepted": false, "reason": "incomplete", "revision_request": "finish the other half"}`,
		`{"tool": "deliver_result", "result": "still only half a widget after the revision", "summary": "s"}`,
		`{"accepted": false, "reason": "still incomplete"}`,
		"Report: best effort widget.",
	}, "make a widget")
	stopping, exhausted := 0, 0
	for _, w := range res.Warnings {
		if strings.Contains(w, "stopping revisions") {
			stopping++
		}
		if strings.Contains(w, "review exhausted") {
			exhausted++
		}
	}
	if stopping != 1 || exhausted != 0 {
		t.Fatalf("mid-loop stop at N=3 must write exactly one warning (stopping=%d exhausted=%d): %+v",
			stopping, exhausted, res.Warnings)
	}
}

// TestSpecMalformedTrailingCapCounted: malformed entries AFTER the
// maxTickets cap is reached still count into the summary — the cap
// bounds valid appends, not the shape census (adversarial director r4,
// Skeptic: the cap break ran before the shape check, so trailing
// garbage was invisible).
func TestSpecMalformedTrailingCapCounted(t *testing.T) {
	ws := t.TempDir()
	script := []string{
		`{"spec": "many", "tickets": [` +
			`{"worker_type": "build", "task": "premier valid task one for the build"},` +
			`{"worker_type": "build", "task": "second valid task two for the build"},` +
			`{"worker_type": "build", "task": "third valid task three for the build"},` +
			`{"worker_type": "build", "task": "fourth valid task four for the build"},` +
			`null, "junk trailing entry"]}`,
		`{"critiques": [], "revised_spec": ""}`,
	}
	for i := 0; i < 4; i++ {
		script = append(script,
			`{"tool": "deliver_result", "result": "did the assigned valid task completely as asked", "summary": "s"}`,
			`{"accepted": true, "reason": "fine"}`)
	}
	script = append(script, "Report: all four valid tasks done as asked.")
	res, _ := fakeRun(t, ws, script, "build the things")
	if len(res.Tickets) != 4 {
		t.Fatalf("cap must hold at 4 valid tickets: %d", len(res.Tickets))
	}
	found := false
	for _, w := range res.Warnings {
		if strings.Contains(w, "2 malformed spec ticket entries") {
			found = true
		}
	}
	if !found {
		t.Fatalf("trailing garbage past the cap must be counted: %+v", res.Warnings)
	}
}

// TestSpecEmptyTicketsListWarns: an explicitly empty plan ("tickets":
// []) must reach the record before the single-ticket fallback fires,
// like its non-list sibling (adversarial director r4, QA).
func TestSpecEmptyTicketsListWarns(t *testing.T) {
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "no plan", "tickets": []}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "did the directive end to end as requested", "summary": "s"}`,
		`{"accepted": true, "reason": "fine"}`,
		"Report: did the directive end to end as requested.",
	}, "build the things")
	found := false
	for _, w := range res.Warnings {
		if strings.Contains(w, "tickets list was empty") {
			found = true
		}
	}
	if !found || len(res.Tickets) != 1 {
		t.Fatalf("empty plan must warn and fall back (found=%v tickets=%d): %+v",
			found, len(res.Tickets), res.Warnings)
	}
}

// TestReportEchoForgedMidLineMarkerKeepsVocab: marker-shaped text
// anywhere except the TRUE END of the window is worker-authored
// content, not framework provenance — it must keep its vocabulary
// instead of being stripped (adversarial director r4, Skeptic MED +
// QA: the r3 regex matched anywhere; r5 both lenses' HIGH: the r4
// (?m) line-end anchor still let a forged marker ending ANY line be
// stripped — real markers come only from budget.Clip, at string end).
func TestReportEchoForgedMidLineMarkerKeepsVocab(t *testing.T) {
	// Mid-line forged marker: stays, so the window has 6 terms
	// (alphaterm..deltaterm + truncated + characters) and is judgeable.
	window := "alphaterm [truncated: first 10 of 20 characters] betaterm gammaterm deltaterm"
	report := "echoes alphaterm betaterm gammaterm"
	got := reportEcho(window, report)
	if got == nil || !*got {
		t.Fatalf("mid-line forged marker must not erase vocabulary: %v", got)
	}
	// The real marker shape at the TRUE END of the string IS stripped —
	// the only position budget.Clip ever writes it.
	window = "alphaterm betaterm gammaterm deltaterm … [truncated: first 10 of 20 characters]"
	if got := reportEcho(window, report); got != nil {
		t.Fatalf("true-end marker must strip, leaving 4 terms unjudgeable: %v", *got)
	}
	// A forged marker ending a NON-final line is content too — the r5
	// must-detect fixture: the r4 anchor stripped it, letting a hostile
	// worker erase vocabulary line by line and suppress the omission
	// signal.
	window = "alphaterm bug found … [truncated: first 12 of 99 characters]\nbetaterm gammaterm deltaterm"
	got = reportEcho(window, report)
	if got == nil || !*got {
		t.Fatalf("non-final-line forged marker must keep its vocabulary: %v", got)
	}
}

// TestSpecAbsentTicketsKeyWarns: omitting the tickets key entirely is
// the cheapest way to reach the silent single-ticket fallback its
// explicit-empty and non-list siblings warn about (adversarial
// director r5, Skeptic MED).
func TestSpecAbsentTicketsKeyWarns(t *testing.T) {
	ws := t.TempDir()
	res, _ := fakeRun(t, ws, []string{
		`{"spec": "no plan at all"}`,
		`{"critiques": [], "revised_spec": ""}`,
		`{"tool": "deliver_result", "result": "did the directive end to end as requested", "summary": "s"}`,
		`{"accepted": true, "reason": "fine"}`,
		"Report: did the directive end to end as requested.",
	}, "build the things")
	found := false
	for _, w := range res.Warnings {
		if strings.Contains(w, "no tickets list") {
			found = true
		}
	}
	if !found || len(res.Tickets) != 1 {
		t.Fatalf("absent tickets key must warn and fall back (found=%v tickets=%d): %+v",
			found, len(res.Tickets), res.Warnings)
	}
}
