package contracts

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The committed contracts directory lives at go/contracts (two levels up from
// this package). Tests run against it so the regeneration gate is real.
func committed(t *testing.T) Dir {
	t.Helper()
	d, err := filepath.Abs(filepath.Join("..", "..", "contracts"))
	if err != nil {
		t.Fatal(err)
	}
	return Dir(d)
}

// Regeneration diff IS the review: the committed generated files must equal
// fresh generation (source ref excepted). If this fails, run
// `maro-go contracts gen` and commit the result in the same change.
func TestCommittedGeneratedFilesDoNotDrift(t *testing.T) {
	drift, err := Drift(committed(t), GenerateAll("test"))
	if err != nil {
		t.Fatal(err)
	}
	if len(drift) > 0 {
		t.Fatalf("contract drift:\n%s", strings.Join(drift, "\n"))
	}
}

// Every registered kind has a declared file with a lifecycle, and the report
// has no errors. Warnings are allowed (undefined is a warning by design) but
// are printed so they are never silent.
func TestCommittedPairReportHasNoErrors(t *testing.T) {
	dir := committed(t)
	gens := GenerateAll("test")
	for _, g := range gens {
		dc, err := dir.ReadDeclared(g.Kind)
		if err != nil {
			t.Fatal(err)
		}
		if dc == nil {
			t.Fatalf("%s: no declared file — step-1 kinds ship declared", g.Kind)
		}
	}
	fs, err := Report(dir, gens)
	if err != nil {
		t.Fatal(err)
	}
	if e := Errors(fs); len(e) > 0 {
		t.Fatalf("contract errors:\n%s", Render(e))
	}
	t.Logf("report (warnings are honest, never silenced):\n%s", Render(fs))
}

func samples() map[record.Kind]any {
	h := record.Header{ID: record.NewID(), Seq: 7, Subject: record.Ref{Kind: "workspace", ID: "root"}, At: time.Now().UTC()}
	return map[record.Kind]any{
		record.KindLease:         &record.LeaseRecord{Header: withSchema(h, "lease/1"), PID: 4242, Epoch: 3, Host: "mini"},
		record.KindThoughtStored: &record.ThoughtStored{Header: withSchema(h, "thought_stored/1"), Hash: "s256v1:" + strings.Repeat("ab", 32), Thought: "goal", Bytes: 12, Encoding: "utf8"},
	}
}

func withSchema(h record.Header, s record.SchemaVer) record.Header { h.Schema = s; return h }

// The provider reads its own payloads as a client must: newer and older.
func TestReferenceReaderForwardAndBackward(t *testing.T) {
	dir := committed(t)
	for _, s := range record.All() {
		sample, ok := samples()[s.Kind]
		if !ok {
			t.Fatalf("no sample for registered kind %s — every kind ships a reference-reader sample", s.Kind)
		}
		gen, err := dir.ReadGenerated(string(s.Kind))
		if err != nil {
			t.Fatal(err)
		}
		dec, err := dir.ReadDeclared(string(s.Kind))
		if err != nil {
			t.Fatal(err)
		}
		if err := ForwardRead(s, sample, dec); err != nil {
			t.Fatal(err)
		}
		if err := BackwardRead(s, sample, gen, dec); err != nil {
			t.Fatal(err)
		}
	}
}

// Must-detect fixtures for the report: shapes built to evade it. "Found 0
// errors" is untrusted until these prove the report can find.
func TestReportMustDetect(t *testing.T) {
	tmp := Dir(t.TempDir())
	gens := GenerateAll("test")
	if err := WriteGenerated(tmp, gens); err != nil {
		t.Fatal(err)
	}
	write := func(kind string, d Declared) {
		raw, _ := json.MarshalIndent(d, "", "  ")
		if err := os.WriteFile(tmp.decPath(kind), raw, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	base := func() Declared {
		return Declared{Kind: "thought_stored", Lifecycle: Stable, Provenance: Inferred, Fields: map[string]DeclaredField{
			"hash":         {Absence: "never", UsedFor: "identity", Constraint: Unconstrained, UnknownValue: "rejected"}, // a thought field: unconstrained by decree (D16)
			"thought_kind": {Absence: "never", UsedFor: "routing", UnknownValue: "rejected", Constraint: Defined, Pattern: "^(goal|prompt|response|step_result|deliverable|lesson_text)$"},
			"bytes":        {Absence: "never", Constraint: Unconstrained},
			"encoding":     {Absence: "never", UsedFor: "routing", UnknownValue: "rejected", Constraint: Defined, Pattern: "^(utf8|bytes)$"},
		}}
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
		{"defined without pattern", func(d *Declared) { f := d.Fields["thought_kind"]; f.Pattern = ""; d.Fields["thought_kind"] = f }, "without a pattern"},
		{"thought field declared defined", func(d *Declared) {
			f := d.Fields["hash"]
			f.Constraint = Defined
			f.Pattern = "^s256v1:"
			d.Fields["hash"] = f
		}, "unconstrained by decree"},
		{"design-pending with generation", func(d *Declared) { d.Lifecycle = DesignPending }, "design-pending"},
	}
	for _, c := range cases {
		d := base()
		c.mut(&d)
		write("thought_stored", d)
		fs, err := Report(tmp, gens)
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
	// Negative control: the clean declared file produces no errors.
	write("thought_stored", base())
	fs, _ := Report(tmp, gens)
	for _, e := range Errors(fs) {
		if e.Kind == "thought_stored" {
			t.Fatalf("negative control: %s", e.Msg)
		}
	}
	// hardened-legacy needs a design flag; lifecycle vocabulary is closed.
	d := base()
	d.Lifecycle = HardenedLegacy
	write("thought_stored", d)
	if _, err := tmp.ReadDeclared("thought_stored"); err == nil {
		t.Fatal("hardened-legacy without design_flag accepted")
	}
	d.Lifecycle = "retired"
	write("thought_stored", d)
	if _, err := tmp.ReadDeclared("thought_stored"); err == nil {
		t.Fatal("unknown lifecycle accepted")
	}
}

// A missing declared file is normal and yields warnings, never silence and
// never a crash; a thought field left undeclared is an ERROR (D16 — undeclared
// is not unconstrained).
func TestUndeclaredIsWarnedNotSilenced(t *testing.T) {
	tmp := Dir(t.TempDir())
	gens := GenerateAll("test")
	_ = WriteGenerated(tmp, gens)
	fs, err := Report(tmp, gens)
	if err != nil {
		t.Fatal(err)
	}
	warnings := 0
	for _, f := range fs {
		if f.Severity == "warning" {
			warnings++
		}
	}
	if warnings == 0 {
		t.Fatal("no declared files must produce warnings")
	}
}

func TestGeneratedShapeIsDerivedFromTheType(t *testing.T) {
	g := generate(mustSpec(t, record.KindThoughtStored), "x")
	wires := map[string]GeneratedField{}
	for _, f := range g.Fields {
		wires[f.Wire] = f
	}
	if f, ok := wires["hash"]; !ok || !f.IsThought {
		t.Fatalf("hash on ThoughtStored must be recognised as a thought field: %+v", wires["hash"])
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

func mustSpec(t *testing.T, k record.Kind) record.Spec {
	s, ok := record.Lookup(k)
	if !ok {
		t.Fatal(k)
	}
	return s
}
