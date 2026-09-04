package contracts

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

func committed(t *testing.T) (Dir, string) {
	t.Helper()
	d, err := filepath.Abs(filepath.Join("..", "..", "contracts"))
	if err != nil {
		t.Fatal(err)
	}
	root, _ := filepath.Abs(filepath.Join("..", ".."))
	return Dir(d), root
}

// Regeneration diff IS the review: committed generated files must equal fresh
// generation byte-for-byte (source ref excepted), no orphans, manifest intact.
func TestCommittedGeneratedFilesDoNotDrift(t *testing.T) {
	dir, _ := committed(t)
	drift, err := Drift(dir, GenerateAll("test"))
	if err != nil {
		t.Fatal(err)
	}
	if len(drift) > 0 {
		t.Fatalf("contract drift:\n%s", strings.Join(drift, "\n"))
	}
}

// Every registered kind has a declared file; the report has no errors; and
// the warning set is a durable expectation (currently empty — a regression
// that stops producing warnings elsewhere is caught by the must-detects).
func TestCommittedPairReportHasNoErrorsAndExpectedWarnings(t *testing.T) {
	dir, root := committed(t)
	gens := GenerateAll("test")
	for _, g := range gens {
		dc, err := dir.ReadDeclared(g.Kind)
		if err != nil {
			t.Fatal(err)
		}
		if dc == nil {
			t.Fatalf("%s: no declared file — every kind ships declared", g.Kind)
		}
	}
	fs, err := Report(dir, gens, root)
	if err != nil {
		t.Fatal(err)
	}
	if e := Errors(fs); len(e) > 0 {
		t.Fatalf("contract errors:\n%s", Render(e))
	}
	var got []string
	for _, w := range Warnings(fs) {
		got = append(got, w.Kind+"/"+w.Field+"/"+w.Dim)
	}
	want := []string{} // the committed step-1 pair declares every dimension
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("warning set changed — update this expectation deliberately:\n%s", Render(Warnings(fs)))
	}
}

func samples() map[record.Kind]any {
	h := record.Header{ID: record.NewID(), Seq: 7, RunID: "run-1", Attempt: 2, Subject: record.Ref{Kind: "workspace", ID: "root"}, Supersedes: record.NewID(), At: time.Now().UTC()}
	ref := thought.Ref{Hash: "s256v1:" + strings.Repeat("ab", 32), Kind: thought.Prompt, Bytes: 3, Encoding: thought.UTF8}
	inv := record.NewID()
	caps := invoke.Capabilities{Name: "scripted", Model: "m", ActsOutward: true}
	return map[record.Kind]any{
		record.KindLease:            &record.LeaseRecord{Header: withSchema(h, "lease/1"), PID: 4242, Epoch: 3, Host: "mini"},
		record.KindThoughtStored:    &record.ThoughtStored{Header: withSchema(h, "thought_stored/1"), Hash: "s256v1:" + strings.Repeat("ab", 32), Thought: "goal", Bytes: 12, Encoding: "utf8"},
		invoke.KindInvocation:       &invoke.Invocation{Header: withSchema(h, "invocation/1"), Purpose: invoke.PurposeExecute, Request: ref, Backend: caps, EffectToken: strings.Repeat("ab", 16), TargetName: "step", TargetLimit: 10, TargetWhy: "p90"},
		invoke.KindDispatched:       &invoke.Dispatched{Header: withSchema(h, "invocation_dispatched/1"), Invocation: inv},
		invoke.KindToolEffect:       &invoke.ToolEffect{Header: withSchema(h, "tool_effect/1"), Invocation: inv, Ordinal: 0, Op: "Read", Class: invoke.OpQuery, Key: strings.Repeat("cd", 32), Input: ref},
		invoke.KindToolEffectResult: &invoke.ToolEffectResult{Header: withSchema(h, "tool_effect_result/1"), Invocation: inv, Ordinal: 0, Output: ref},
		invoke.KindTerminalObserved: &invoke.TerminalObserved{Header: withSchema(h, "terminal_observed/1"), Invocation: inv, Attempt: 1, State: invoke.TerminalComplete, Reason: "ok", Response: &ref, Transcript: &ref, Usage: invoke.Usage{InputTokens: 1}},
		invoke.KindReceipt:          &invoke.Receipt{Header: withSchema(h, "receipt/1"), Invocation: inv, Attempt: 1, Response: ref, Usage: invoke.Usage{InputTokens: 1, CostUSD: 0.5, CostReported: true}},
		verdict.KindObservation:     &verdict.Observation{Header: withSchema(h, "observation/1"), Check: verdict.CheckPathExists, Claim: ref, Result: verdict.Refuted, Confidence: 0.9, Evidence: []record.Ref{{Kind: "thought", ID: "x"}}},
		verdict.KindVerdict:         &verdict.Verdict{Header: withSchema(h, "verdict/1"), VerdictKind: verdict.KindClosure, Outcome: "achieved", Confidence: 0.7, Source: verdict.Source{Standing: verdict.StandingJudge, Ref: inv}, Basis: []record.Ref{{Kind: "receipt", ID: "r"}}, Falsifiers: []thought.Ref{ref}, Direction: verdict.Both},
		verdict.KindResolution:      &verdict.Resolution{Header: withSchema(h, "resolution/1"), VerdictKind: verdict.KindClosure, Outcome: "achieved", Effective: inv, Candidates: []record.RecordID{inv}, Observations: []record.RecordID{inv}, ResolverVer: verdict.ResolverVer, Rule: "standing:judge", Confidence: 0.7},
		invoke.KindReconciled:       &invoke.Reconciled{Header: withSchema(h, "invocation_reconciled/1"), Invocation: inv, Disposition: invoke.DispositionAbandoned, Evidence: "tool-less"},
	}
}

func withSchema(h record.Header, s record.SchemaVer) record.Header { h.Schema = s; return h }

// The provider reads its own payloads as a client must, and every declared
// rule must actually be exercised.
func TestReferenceReaderExercisesEveryDeclaredRule(t *testing.T) {
	dir, _ := committed(t)
	for _, s := range record.All() {
		sample, ok := samples()[s.Kind]
		if !ok {
			t.Fatalf("no sample for registered kind %s", s.Kind)
		}
		gen, err := dir.ReadGenerated(string(s.Kind))
		if err != nil {
			t.Fatal(err)
		}
		dec, err := dir.ReadDeclared(string(s.Kind))
		if err != nil {
			t.Fatal(err)
		}
		fx, err := ForwardRead(s, sample, dec)
		if err != nil {
			t.Fatal(err)
		}
		bx, err := BackwardRead(s, sample, gen, dec)
		if err != nil {
			t.Fatal(err)
		}
		wantUV, wantPat := 0, 0
		for _, f := range dec.Fields {
			if f.UnknownValue != "" {
				wantUV++
			}
			if f.Pattern != "" {
				wantPat++
			}
		}
		// unknown_value rules apply to string-valued fields only; the header
		// declares some on non-string wires (seq), which are skipped honestly.
		if fx.UnknownValueRules == 0 || fx.PatternRules != wantPat || !fx.ForwardUnknownField {
			t.Fatalf("%s forward exercised %+v (declared unknown_value=%d patterns=%d)", s.Kind, fx, wantUV, wantPat)
		}
		if bx.RemovedOptionals == 0 || bx.OnAbsenceRules == 0 {
			t.Fatalf("%s backward exercised %+v", s.Kind, bx)
		}
	}
}

func writeDeclared(t *testing.T, dir Dir, kind string, d Declared) {
	t.Helper()
	raw, _ := json.MarshalIndent(d, "", "  ")
	if err := os.WriteFile(dir.decPath(kind), raw, 0o644); err != nil {
		t.Fatal(err)
	}
}

func baseDeclared() Declared {
	return Declared{Kind: "thought_stored", Lifecycle: Stable, Provenance: Inferred, Fields: map[string]DeclaredField{
		"hash":         {Absence: "never", UsedFor: "identity", Constraint: Unconstrained, UnknownValue: "rejected"},
		"thought_kind": {Absence: "never", UsedFor: "routing", UnknownValue: "rejected", Constraint: Defined, Pattern: "^(goal|prompt)$", Rejects: "chunk"},
		"bytes":        {Absence: "never", Constraint: Unconstrained},
		"encoding":     {Absence: "never", UsedFor: "routing", UnknownValue: "rejected", Constraint: Defined, Pattern: "^(utf8|bytes)$", Rejects: "latin1"},
	}}
}

// Must-detect fixtures: shapes built to evade the report. "Found 0 errors"
// is untrusted until these prove the report can find.
func TestReportMustDetect(t *testing.T) {
	tmp := Dir(t.TempDir())
	gens := GenerateAll("test")
	if err := WriteGenerated(tmp, gens); err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name string
		mut  func(*Declared)
		want string
	}{
		{"declared field not generated", func(d *Declared) { d.Fields["ghost"] = DeclaredField{Absence: "never"} }, "declared field not in generated"},
		{"authorization + accepted-unchanged", func(d *Declared) {
			f := d.Fields["encoding"]
			f.UsedFor, f.UnknownValue = "authorization", "accepted-unchanged"
			d.Fields["encoding"] = f
		}, "illegal combination"},
		{"defined without pattern", func(d *Declared) {
			f := d.Fields["thought_kind"]
			f.Pattern, f.Rejects = "", ""
			d.Fields["thought_kind"] = f
		}, "without a pattern"},
		{"thought field declared defined", func(d *Declared) {
			f := d.Fields["hash"]
			f.Constraint, f.Pattern, f.Rejects = Defined, "^s256v1:", "x"
			d.Fields["hash"] = f
		}, "unconstrained by decree"},
		{"design-pending with generation", func(d *Declared) { d.Lifecycle = DesignPending }, "design-pending"},
		{"measured_by does not resolve", func(d *Declared) { d.MeasuredBy = "internal/thought:TestDoesNotExist" }, "does not resolve"},
	}
	for _, c := range cases {
		d := baseDeclared()
		c.mut(&d)
		writeDeclared(t, tmp, "thought_stored", d)
		fs, err := Report(tmp, gens, filepath.Join("..", ".."))
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		found := false
		for _, e := range Errors(fs) {
			if strings.Contains(e.Msg, c.want) {
				found = true
			}
		}
		if !found {
			t.Fatalf("%s: report did not detect it:\n%s", c.name, Render(fs))
		}
	}
	// Negative control: the clean declared file produces no thought_stored errors.
	writeDeclared(t, tmp, "thought_stored", baseDeclared())
	fs, _ := Report(tmp, gens, filepath.Join("..", ".."))
	for _, e := range Errors(fs) {
		if e.Kind == "thought_stored" {
			t.Fatalf("negative control: %s", e.Msg)
		}
	}
}

// Load-time must-detects: closed vocabularies, strict keys, compiled and
// falsifiable patterns, lifecycle rules.
func TestDeclaredLoadMustDetect(t *testing.T) {
	tmp := Dir(t.TempDir())
	raw := func(s string) {
		if err := os.WriteFile(tmp.decPath("thought_stored"), []byte(s), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	bad := []struct{ name, body, want string }{
		{"unknown lifecycle", `{"kind":"thought_stored","lifecycle":"retired","provenance":"supplied","fields":{}}`, "bad lifecycle"},
		{"hardened without flag", `{"kind":"thought_stored","lifecycle":"hardened-legacy","provenance":"supplied","fields":{}}`, "design_flag"},
		{"bad provenance", `{"kind":"thought_stored","lifecycle":"stable","provenance":"guessed","fields":{}}`, "closed vocabulary"},
		{"bad on_absence", `{"kind":"thought_stored","lifecycle":"stable","provenance":"supplied","fields":{"bytes":{"on_absence":"toleratted"}}}`, "closed vocabulary"},
		{"bad used_for", `{"kind":"thought_stored","lifecycle":"stable","provenance":"supplied","fields":{"bytes":{"used_for":"fun"}}}`, "closed vocabulary"},
		{"pattern does not compile", `{"kind":"thought_stored","lifecycle":"stable","provenance":"supplied","fields":{"encoding":{"pattern":"(","rejects":"x"}}}`, "does not compile"},
		{"pattern without rejects", `{"kind":"thought_stored","lifecycle":"stable","provenance":"supplied","fields":{"encoding":{"pattern":"^a$"}}}`, "no `rejects`"},
		{"rejects matches pattern", `{"kind":"thought_stored","lifecycle":"stable","provenance":"supplied","fields":{"encoding":{"pattern":"^a$","rejects":"a"}}}`, "MATCHES"},
		{"trailing content", `{"kind":"thought_stored","lifecycle":"stable","provenance":"supplied","fields":{}} {}`, "trailing content"},
	}
	for _, c := range bad {
		raw(c.body)
		_, err := tmp.ReadDeclared("thought_stored")
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: want %q, got %v", c.name, c.want, err)
		}
	}
}

// Drift must-detects: a hand edit, an extra key, an orphan, a missing manifest.
func TestDriftMustDetect(t *testing.T) {
	gens := GenerateAll("test")
	fresh := func() Dir {
		d := Dir(t.TempDir())
		if err := WriteGenerated(d, gens); err != nil {
			t.Fatal(err)
		}
		return d
	}
	d := fresh()
	if drift, _ := Drift(d, gens); len(drift) != 0 {
		t.Fatalf("clean generation drifts: %v", drift)
	}
	// hand edit that keeps the struct identical but breaks canonical bytes
	d = fresh()
	raw, _ := os.ReadFile(d.genPath("lease"))
	os.WriteFile(d.genPath("lease"), append([]byte("\n"), raw...), 0o644)
	if drift, _ := Drift(d, gens); len(drift) == 0 {
		t.Fatal("non-canonical hand edit not detected")
	}
	// extra key
	d = fresh()
	raw, _ = os.ReadFile(d.genPath("lease"))
	os.WriteFile(d.genPath("lease"), []byte(strings.Replace(string(raw), `"kind": "lease",`, `"kind": "lease", "note": "edited",`, 1)), 0o644)
	if drift, _ := Drift(d, gens); len(drift) == 0 {
		t.Fatal("extra key not detected")
	}
	// orphan
	d = fresh()
	os.WriteFile(d.genPath("ghost"), []byte("{}"), 0o644)
	if drift, _ := Drift(d, gens); len(drift) == 0 || !strings.Contains(strings.Join(drift, "\n"), "orphan") {
		t.Fatalf("orphan not detected: %v", drift)
	}
	// missing manifest
	d = fresh()
	os.Remove(filepath.Join(string(d), manifestFile))
	if drift, _ := Drift(d, gens); len(drift) == 0 || !strings.Contains(strings.Join(drift, "\n"), "MANIFEST") {
		t.Fatalf("missing manifest not detected: %v", drift)
	}
}

// A missing declared file is normal and yields warnings, never silence and
// never a crash; a thought field left undeclared is an ERROR.
func TestUndeclaredIsWarnedNotSilenced(t *testing.T) {
	tmp := Dir(t.TempDir())
	gens := GenerateAll("test")
	_ = WriteGenerated(tmp, gens)
	fs, err := Report(tmp, gens, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(Warnings(fs)) == 0 {
		t.Fatal("no declared files must produce warnings")
	}
	thoughtErr := false
	for _, e := range Errors(fs) {
		if e.Field == "hash" {
			thoughtErr = true
		}
	}
	if !thoughtErr {
		t.Fatal("undeclared thought field must be an error")
	}
}

// The reference reader refuses a fixture that cannot fail.
func TestReferenceReaderRefusesVacuousFixture(t *testing.T) {
	dir, _ := committed(t)
	s, _ := record.Lookup(record.KindLease)
	gen, _ := dir.ReadGenerated("lease")
	dec, _ := dir.ReadDeclared("lease")
	// a sample with no optionals present: nothing can be removed
	h := record.Header{ID: record.NewID(), Seq: 1, Subject: record.Ref{Kind: "workspace", ID: "root"}, At: time.Now(), Schema: "lease/1"}
	thin := &record.LeaseRecord{Header: h, PID: 1, Epoch: 1}
	if _, err := BackwardRead(s, thin, gen, dec); err == nil || !strings.Contains(err.Error(), "cannot fail") {
		t.Fatalf("vacuous backward fixture accepted: %v", err)
	}
	// header.supersedes has a pattern; a sample without it cannot exercise it
	if _, err := ForwardRead(s, thin, dec); err == nil || !strings.Contains(err.Error(), "no value to test") {
		t.Fatalf("pattern with no sample value accepted: %v", err)
	}
}

func TestGeneratedShapeIsDerivedFromTheType(t *testing.T) {
	s, _ := record.Lookup(record.KindThoughtStored)
	g := generate(s, "x")
	wires := map[string]GeneratedField{}
	for _, f := range g.Fields {
		wires[f.Wire] = f
	}
	if f, ok := wires["hash"]; !ok || !f.IsThought {
		t.Fatalf("hash must be recognised as a thought field: %+v", wires["hash"])
	}
	if f, ok := wires["header.supersedes"]; !ok || !f.Omittable || !f.FromHeader {
		t.Fatalf("header.supersedes should be an omittable header field: %+v", f)
	}
	if _, ok := wires["ProductionRecord"]; ok {
		t.Fatal("envelope marker leaked into the wire contract")
	}
	if g.Envelope != "production" || g.Schema != "thought_stored/1" {
		t.Fatalf("%+v", g)
	}
}

// Contract input §19–§20: readers of pair files tolerate unknown keys (a
// vocabulary WARNING naming key and path, never a refusal); x- keys are
// legal only with a register row; the committed pair pins zero of either.
func TestUnknownAndImprovisedKeys(t *testing.T) {
	tmp := Dir(t.TempDir())
	gens := GenerateAll("test")
	_ = WriteGenerated(tmp, gens)
	raw := func(s string) {
		if err := os.WriteFile(tmp.decPath("thought_stored"), []byte(s), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// a typo at field level and a bare unknown at top level: both warned, file still read
	raw(`{"kind":"thought_stored","lifecycle":"stable","provenance":"supplied","notes":"hi","fields":{"hash":{"absence":"never","constraint":"unconstrained","on_absense":"rejected"}}}`)
	dc, err := tmp.ReadDeclared("thought_stored")
	if err != nil || dc == nil {
		t.Fatalf("tolerant read failed: %v", err)
	}
	if len(dc.Unknown) != 2 || dc.Unknown[0].Path != "fields.hash.on_absense" || dc.Unknown[1].Key != "notes" {
		t.Fatalf("unknown keys: %+v", dc.Unknown)
	}
	if dc.FormatVersion != FormatVersion {
		t.Fatalf("missing format_version must read as the founding revision, got %q", dc.FormatVersion)
	}
	fs, _ := Report(tmp, gens, "")
	vocab := 0
	for _, f := range Warnings(fs) {
		if f.Dim == "vocabulary" {
			vocab++
		}
	}
	if vocab != 2 {
		t.Fatalf("expected 2 vocabulary warnings, got %d:\n%s", vocab, Render(fs))
	}
	// an x- key without a register row is an ERROR; with one it is legal and kept verbatim
	raw(`{"kind":"thought_stored","lifecycle":"stable","provenance":"supplied","fields":{"hash":{"absence":"never","constraint":"unconstrained","x-status-variants":["a","b"]}}}`)
	fs, _ = Report(tmp, gens, "")
	found := false
	for _, e := range Errors(fs) {
		if strings.Contains(e.Msg, "without a register row") {
			found = true
		}
	}
	if !found {
		t.Fatalf("unregistered x- key not caught:\n%s", Render(fs))
	}
	raw(`{"kind":"thought_stored","lifecycle":"stable","provenance":"supplied","improvised":{"x-status-variants":{"used_at":"field:hash","search":"grep -r status-variants contracts/"}},"fields":{"hash":{"absence":"never","constraint":"unconstrained","x-status-variants":["a","b"]}}}`)
	dc, _ = tmp.ReadDeclared("thought_stored")
	if string(dc.Fields["hash"].Extra["x-status-variants"]) != `["a","b"]` || len(dc.UnregisteredImprovised()) != 0 {
		t.Fatalf("registered x- key not kept verbatim: %+v", dc.Fields["hash"].Extra)
	}
	fs, _ = Report(tmp, gens, "")
	for _, e := range Errors(fs) {
		if strings.Contains(e.Msg, "register") {
			t.Fatal("registered x- key reported as an error")
		}
	}
}

// The committed pair carries no unknown keys and no unregistered improvised
// keys — the pin the practice says discipline cannot replace.
func TestCommittedPairHasNoBareKeys(t *testing.T) {
	dir, _ := committed(t)
	for _, g := range GenerateAll("test") {
		dc, err := dir.ReadDeclared(g.Kind)
		if err != nil {
			t.Fatal(err)
		}
		if len(dc.Unknown) != 0 || len(dc.UnregisteredImprovised()) != 0 {
			t.Fatalf("%s: unknown=%v unregistered=%v", g.Kind, dc.Unknown, dc.UnregisteredImprovised())
		}
		if dc.FormatVersion == "" {
			t.Fatalf("%s: no format_version", g.Kind)
		}
	}
	for _, g := range GenerateAll("test") {
		have, _ := dir.ReadGenerated(g.Kind)
		if have.FormatVersion != FormatVersion {
			t.Fatalf("%s generated at format %q", g.Kind, have.FormatVersion)
		}
	}
}

// A regeneration diff is CLASSIFIED: added field → additive; removed,
// retyped, presence-flipped → breaking (the standard's rule 2).
func TestClassifyRegenerationDiff(t *testing.T) {
	base := Generated{Kind: "k", Envelope: "production", Schema: "k/1", Fields: []GeneratedField{
		{Wire: "a", GoType: "string"}, {Wire: "b", GoType: "int64", Omittable: true},
	}}
	clone := func() Generated { g := base; g.Fields = append([]GeneratedField{}, base.Fields...); return g }
	if c := Classify(base, clone()); c.Class != "none" {
		t.Fatalf("identical: %+v", c)
	}
	g := clone()
	g.Fields = append(g.Fields, GeneratedField{Wire: "c", GoType: "bool", Omittable: true})
	if c := Classify(base, g); c.Class != "additive" {
		t.Fatalf("added field: %+v", c)
	}
	g = clone()
	g.Fields = g.Fields[:1]
	if c := Classify(base, g); c.Class != "breaking" || !strings.Contains(c.Details[0], "removed") {
		t.Fatalf("removed field: %+v", c)
	}
	g = clone()
	g.Fields[0].GoType = "int"
	if c := Classify(base, g); c.Class != "breaking" {
		t.Fatalf("retyped: %+v", c)
	}
	g = clone()
	g.Fields[1].Omittable = false
	if c := Classify(base, g); c.Class != "breaking" {
		t.Fatalf("presence flip: %+v", c)
	}
	g = clone()
	g.Fields[0].Wire = "renamed"
	if c := Classify(base, g); c.Class != "breaking" {
		t.Fatalf("rename must be breaking (remove + add): %+v", c)
	}
	// and Drift names the class on a real committed dir
	tmp := Dir(t.TempDir())
	gens := GenerateAll("test")
	_ = WriteGenerated(tmp, gens)
	raw, _ := os.ReadFile(tmp.genPath("lease"))
	edited := strings.Replace(string(raw), `"wire": "host"`, `"wire": "hostname"`, 1)
	os.WriteFile(tmp.genPath("lease"), []byte(edited), 0o644)
	drift, _ := Drift(tmp, gens)
	if len(drift) == 0 || !strings.Contains(strings.Join(drift, "\n"), "BREAKING") {
		t.Fatalf("drift did not classify the rename as breaking: %v", drift)
	}
}

// The insufficiency report has exactly six numbered items, never a narrative.
func TestInsufficiencyIsSixItems(t *testing.T) {
	dir, root := committed(t)
	gens := GenerateAll("test")
	fs, _ := Report(dir, gens, root)
	drift, _ := Drift(dir, gens)
	block := Insufficiency(dir, gens, fs, drift)
	for i := 1; i <= 6; i++ {
		if !strings.Contains(block, fmt.Sprintf("%d. ", i)) {
			t.Fatalf("item %d missing:\n%s", i, block)
		}
	}
	if !strings.Contains(block, "6. Deliverable: tests") {
		t.Fatalf("committed pair should yield the tests deliverable:\n%s", block)
	}
}
