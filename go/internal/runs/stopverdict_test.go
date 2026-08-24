package runs

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The stop-verdict tuple is a REPLACEMENT, not a merge, and every one of
// its three members has a failure mode that leaves a plausible-looking
// metadata.json behind. A stale reopen payload annotating fresh evidence
// reads as data. A cleared verdict whose evidence survives reads as a
// verdict. A refine note that got clipped off reads as an overwrite of
// something that was never recorded.
//
// So the differential compares the whole FILE against CPython's, and the
// unit tests below cover the transitions the file comparison cannot reach
// in one shot.

const pyStampSrc = `
import json, sys
from pathlib import Path
from runs import stamp_run_stop_verdict
rd, calls = json.loads(sys.argv[1])
for c in calls:
    ev_out = []
    stamp_run_stop_verdict(
        stop_verdict=c["stop_verdict"],
        stop_evidence=c["stop_evidence"],
        pause_reason=c.get("pause_reason", ""),
        run_dir=Path(rd),
        refine_note=c.get("refine_note", False),
        evidence_out=ev_out,
        reopen_payload=c.get("reopen_payload"),
    )
sys.stdout.write((Path(rd) / "metadata.json").read_text(encoding="utf-8"))
`

type stampCall struct {
	StopVerdict   string         `json:"stop_verdict"`
	StopEvidence  string         `json:"stop_evidence"`
	PauseReason   string         `json:"pause_reason,omitempty"`
	RefineNote    bool           `json:"refine_note,omitempty"`
	ReopenPayload map[string]any `json:"reopen_payload,omitempty"`
}

// seedRun builds a run dir with a metadata.json that already carries the
// fields a real escalated run carries, so a stamp is exercised as a
// MUTATION of an existing file rather than a creation.
//
// Written the same way on both sides of the differential: the seed goes
// through this port's own WriteMetadata, and the Python probe reads the
// file that produced. Transporting the seed as JSON would launder its
// types (Go's decoder makes every number a float64, Python keeps ints
// int) — the transport hazard this port keeps re-earning.
func seedRun(t *testing.T, ws, handleID string) string {
	t.Helper()
	rd, err := Create(ws, handleID, "audit the escalation lane")
	if err != nil {
		t.Fatal(err)
	}
	if err := WriteMetadata(rd, pyval.Obj{
		{Key: "loop_id", Val: "loop-" + handleID},
		{Key: "status", Val: "stuck"},
		{Key: "goal_achieved", Val: false},
		{Key: "iterations", Val: 7},
		{Key: "stop_verdict", Val: "out-of-budget"},
		{Key: "stop_evidence", Val: "iteration cap reached at 7"},
		{Key: "stop_reopen_payload", Val: pyval.Obj{
			{Key: "kind", Val: "budget"},
			{Key: "limit", Val: 7},
		}},
	}); err != nil {
		t.Fatal(err)
	}
	return rd
}

// The whole metadata.json, after the same sequence of stamps, compared
// byte for byte. The sequence is chosen so every branch of the tuple
// owner is live at least once: refine over a different prior, re-stamp
// without a payload (which must POP the one from the seed), a pause
// reason, and a clearing stamp.
func TestStampedMetadataMatchesCPythonByteForByte(t *testing.T) {
	sequences := []struct {
		name  string
		calls []stampCall
	}{
		{"refine over a different prior", []stampCall{{
			StopVerdict:  "reachable-but-not-worth-it",
			StopEvidence: "director escalation close at depth 2 (confidence 8/10): the remaining work is a rewrite",
			RefineNote:   true,
			ReopenPayload: map[string]any{
				"kind": "escalation-close", "depth": 2, "confidence": 8,
			},
		}}},
		{"re-stamp without a payload pops the stale one", []stampCall{{
			StopVerdict:  "thesis-refuted",
			StopEvidence: "three passes produced the same failure",
			RefineNote:   true,
		}}},
		{"same verdict re-stamped takes no refine note", []stampCall{{
			StopVerdict:  "out-of-budget",
			StopEvidence: "the cap again",
			RefineNote:   true,
		}}},
		{"a clearing stamp pops all three", []stampCall{{
			StopVerdict:  "",
			StopEvidence: "ignored",
			RefineNote:   true,
		}}},
		{"pause reason rides along", []stampCall{{
			StopVerdict:  "external-interrupt",
			StopEvidence: "operator stop",
			PauseReason:  "manual-intervention",
		}}},
		{"two stamps in a row", []stampCall{
			{StopVerdict: "lost-the-plot", StopEvidence: "answered a different ask", RefineNote: true},
			{StopVerdict: "reachable-but-not-worth-it", StopEvidence: "cost exceeds value", RefineNote: true,
				ReopenPayload: map[string]any{"kind": "escalation-close", "depth": 3, "confidence": 6}},
		}},
		{"a pause reason is not cleared by a later stamp that omits it", []stampCall{
			{StopVerdict: "external-interrupt", StopEvidence: "box busy", PauseReason: "box-busy"},
			{StopVerdict: "thesis-refuted", StopEvidence: "converged"},
		}},
		{"evidence past the cap is clipped, and the note survives it", []stampCall{{
			StopVerdict:  "reachable-but-not-worth-it",
			StopEvidence: strings.Repeat("why not ", 200),
			RefineNote:   true,
		}}},
		{"the prose that breaks encoding/json", []stampCall{{
			StopVerdict:  "lost-the-plot",
			StopEvidence: "prefer a > b & not c < d in the café path → retry",
			RefineNote:   true,
		}}},
	}

	for _, seq := range sequences {
		t.Run(seq.name, func(t *testing.T) {
			goWS, pyWS := t.TempDir(), t.TempDir()
			const handleID = "20260824T090000-5eeded01"
			goRD := seedRun(t, goWS, handleID)
			pyRD := seedRun(t, pyWS, handleID)
			// Both seeds stamp started_at from the wall clock, so a second
			// ticking between them shows up as a byte difference that has
			// nothing to do with the writer under test. Start the two sides
			// from literally the same bytes.
			seedBytes, err := os.ReadFile(filepath.Join(goRD, "metadata.json"))
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(pyRD, "metadata.json"), seedBytes, 0o644); err != nil {
				t.Fatal(err)
			}

			for _, c := range seq.calls {
				var out string
				var payload pyval.Obj
				for _, k := range sortedPayloadKeys(c.ReopenPayload) {
					payload = append(payload, pyval.Field{Key: k, Val: c.ReopenPayload[k]})
				}
				if _, err := StampRunStopVerdict(StopTupleOptions{
					StopVerdict:   c.StopVerdict,
					StopEvidence:  c.StopEvidence,
					PauseReason:   c.PauseReason,
					RunDir:        goRD,
					RefineNote:    c.RefineNote,
					EvidenceOut:   &out,
					ReopenPayload: payload,
				}); err != nil {
					t.Fatal(err)
				}
			}

			arg, err := json.Marshal([]any{pyRD, seq.calls})
			if err != nil {
				t.Fatal(err)
			}
			want := runPyIn(t, pyWS, pyStampSrc, string(arg))
			raw, err := os.ReadFile(filepath.Join(goRD, "metadata.json"))
			if err != nil {
				t.Fatal(err)
			}
			if string(raw) != want {
				t.Errorf("metadata.json is not CPython's:\n--- go ---\n%s\n--- py ---\n%s\n%s",
					raw, want, firstDiff(string(raw), want))
			}
		})
	}
}

// sortedPayloadKeys keeps the Go-side payload in the SAME order the JSON
// transport hands the Python side, so a key-order difference in the
// rendered file is a real finding rather than an artifact of how the test
// moved its fixture across.
func sortedPayloadKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	for i := 1; i < len(keys); i++ {
		for j := i; j > 0 && keys[j] < keys[j-1]; j-- {
			keys[j], keys[j-1] = keys[j-1], keys[j]
		}
	}
	return keys
}

func firstDiff(a, b string) string {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	for i := 0; i < n; i++ {
		if a[i] != b[i] {
			lo := i - 40
			if lo < 0 {
				lo = 0
			}
			return "first difference at byte " + itoa(i) + ": go " + quote(a[lo:min(len(a), i+40)]) +
				" vs py " + quote(b[lo:min(len(b), i+40)])
		}
	}
	if len(a) != len(b) {
		return "same prefix, different length: go " + itoa(len(a)) + " py " + itoa(len(b))
	}
	return ""
}

func itoa(n int) string {
	b, _ := json.Marshal(n)
	return string(b)
}

func quote(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// EvidenceOut has to be captured INSIDE the lock, and it has to be the
// value that was WRITTEN — clipped, with the refine note attached — not
// the value that was passed in.
//
// Python's round-2 review found this the hard way: the first cut had the
// caller re-read metadata.json after the lock released, and a concurrent
// writer in that window substituted its content into the caller's ledger
// row. The Go signature makes the correct thing the only available thing,
// but the CONTENT still has to be right, which is what this asserts.
func TestEvidenceOutIsTheWrittenValue(t *testing.T) {
	ws := t.TempDir()
	rd := seedRun(t, ws, "20260824T090000-ev1")

	var out string
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict:  "reachable-but-not-worth-it",
		StopEvidence: "cost exceeds value",
		RunDir:       rd,
		RefineNote:   true,
		EvidenceOut:  &out,
	}); err != nil {
		t.Fatal(err)
	}
	if want := "cost exceeds value [refines: out-of-budget]"; out != want {
		t.Errorf("EvidenceOut = %q, want %q", out, want)
	}
	// The value in the file and the value handed back must be the same
	// string. A ledger row built from one and a metadata file holding the
	// other is a reader having to decide which lied.
	meta := readOrderedMeta(t, rd)
	if got := meta.GetString("stop_evidence"); got != out {
		t.Errorf("metadata holds %q but the caller was handed %q", got, out)
	}

	// Long evidence: EvidenceOut must be the CLIPPED value.
	long := strings.Repeat("z", 900)
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict: "thesis-refuted", StopEvidence: long,
		RunDir: rd, EvidenceOut: &out,
	}); err != nil {
		t.Fatal(err)
	}
	if len([]rune(out)) <= 900 && !strings.Contains(out, "truncated") {
		t.Errorf("EvidenceOut looks unclipped (%d runes, no marker)", len([]rune(out)))
	}
	if got := readOrderedMeta(t, rd).GetString("stop_evidence"); got != out {
		t.Error("the clipped value in the file differs from the one handed back")
	}

	// A CLEARING stamp still appends, and what it appends is "" — the
	// field was popped, so there is nothing to report. Python appends
	// `existing.get("stop_evidence", "")` unconditionally.
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict: "", RunDir: rd, EvidenceOut: &out,
	}); err != nil {
		t.Fatal(err)
	}
	if out != "" {
		t.Errorf("a clearing stamp handed back %q; the field was popped", out)
	}
}

// The reopen payload's replace-whole doctrine, stated as the three
// transitions it covers. The middle one is the one a merge-shaped
// implementation gets wrong: re-stamping the SAME verdict without a
// payload must still pop the old payload, because the payload describes
// the stamp that wrote it and a predecessor's numbers standing beside
// fresher evidence is a lie that reads as data.
func TestTheReopenPayloadIsReplacedWhole(t *testing.T) {
	ws := t.TempDir()
	rd := seedRun(t, ws, "20260824T090000-rp1")

	if _, ok := readOrderedMeta(t, rd).Get("stop_reopen_payload"); !ok {
		t.Fatal("the seed has no payload, so this test cannot observe a pop")
	}
	// Same verdict as the seed, no payload supplied.
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict: "out-of-budget", StopEvidence: "again", RunDir: rd,
	}); err != nil {
		t.Fatal(err)
	}
	if _, ok := readOrderedMeta(t, rd).Get("stop_reopen_payload"); ok {
		t.Error("a re-stamp of the same verdict left the previous stamp's payload " +
			"standing beside new evidence")
	}
	// A payload supplied is stored.
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict: "lost-the-plot", StopEvidence: "off-ask", RunDir: rd,
		ReopenPayload: pyval.Obj{{Key: "kind", Val: "demotion"}},
	}); err != nil {
		t.Fatal(err)
	}
	if _, ok := readOrderedMeta(t, rd).Get("stop_reopen_payload"); !ok {
		t.Fatal("a supplied payload was not stored")
	}
	// An EMPTY payload is not a payload — it pops, like a nil one.
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict: "lost-the-plot", StopEvidence: "off-ask", RunDir: rd,
		ReopenPayload: pyval.Obj{},
	}); err != nil {
		t.Fatal(err)
	}
	if _, ok := readOrderedMeta(t, rd).Get("stop_reopen_payload"); ok {
		t.Error("an empty payload was persisted as one")
	}
	// Clearing the verdict clears the payload too.
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict: "lost-the-plot", StopEvidence: "off-ask", RunDir: rd,
		ReopenPayload: pyval.Obj{{Key: "kind", Val: "demotion"}},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict: "", RunDir: rd,
	}); err != nil {
		t.Fatal(err)
	}
	m := readOrderedMeta(t, rd)
	for _, k := range []string{"stop_verdict", "stop_evidence", "stop_reopen_payload"} {
		if _, ok := m.Get(k); ok {
			t.Errorf("a clearing stamp left %s standing", k)
		}
	}
	// And it left the run's OTHER fields alone — this is a tuple owner,
	// not a metadata eraser.
	if got := m.GetString("status"); got != "stuck" {
		t.Errorf("status is now %q; the tuple owner touched a field it does not own", got)
	}
}

// There is deliberately NO vocabulary check in this writer. Python has
// none either, and the validation lives at the ledger stamp instead —
// where an off-vocabulary value fails to UNSTAMPED so status fallbacks
// apply. A test pins the asymmetry so a future reader who notices the
// "missing" check finds out it was a decision.
func TestThisWriterDoesNotPoliceTheVocabulary(t *testing.T) {
	ws := t.TempDir()
	rd := seedRun(t, ws, "20260824T090000-vocab")
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict: "not-a-real-verdict", StopEvidence: "e", RunDir: rd,
	}); err != nil {
		t.Fatal(err)
	}
	if got := readOrderedMeta(t, rd).GetString("stop_verdict"); got != "not-a-real-verdict" {
		t.Errorf("stop_verdict is %q; this writer stamps what it is given, like "+
			"Python's, and the gate lives at the ledger", got)
	}
}

// A stamp is a PUBLISHED metadata mutation, so it has to leave the run
// findable. Python calls index_run_dir from inside the merge for exactly
// this reason — the bypass burn-down that created this owner found a
// caller doing a bare locked_rmw that skipped indexing, leaving the run
// index holding a stale row after a close.
func TestAStampPublishesTheIndex(t *testing.T) {
	ws := t.TempDir()
	rd := seedRun(t, ws, "20260824T090000-idx")
	// Take the index away, so the loop id is reachable only by a scan and
	// the publish this test is about is the one the STAMP does. (The seed
	// goes through WriteMetadata, which publishes too — removing it is what
	// keeps this test about the stamp rather than about the seed.)
	if err := os.RemoveAll(filepath.Join(ws, indexDirName)); err != nil {
		t.Fatal(err)
	}
	if isDir(filepath.Join(ws, indexDirName)) {
		t.Fatal("the index directory survived being removed")
	}
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict: "thesis-refuted", StopEvidence: "converged", RunDir: rd,
	}); err != nil {
		t.Fatal(err)
	}
	entry := indexEntryPath("loop-20260824T090000-idx", RunsRoot(ws))
	if !isFile(entry) {
		t.Error("the stamp did not publish the run's references; a later lookup " +
			"by loop id pays for a full scan, or misses")
	}
}

// A run dir is REQUIRED here, where Python defaults to the process's
// active run. The Go port has no ambient current run, and a default would
// make the "stamp a run that ended elsewhere" case — which is the only
// case this writer has — silently target the wrong directory.
func TestAMissingRunDirIsRefusedRatherThanGuessed(t *testing.T) {
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict: "thesis-refuted", StopEvidence: "e",
	}); err == nil {
		t.Error("a stamp with no run dir succeeded")
	}
}

// Corrupt metadata degrades to a fresh object rather than wedging the
// stamp: the superseded state is lost either way, and refusing to write
// would additionally lose the NEW verdict.
func TestACorruptMetadataStillTakesTheStamp(t *testing.T) {
	ws := t.TempDir()
	rd := seedRun(t, ws, "20260824T090000-corrupt")
	if err := os.WriteFile(filepath.Join(rd, "metadata.json"),
		[]byte("{not json at all"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict: "external-interrupt", StopEvidence: "power loss", RunDir: rd,
	}); err != nil {
		t.Fatal(err)
	}
	if got := readOrderedMeta(t, rd).GetString("stop_verdict"); got != "external-interrupt" {
		t.Errorf("stop_verdict is %q after stamping over corrupt metadata", got)
	}
}

func readOrderedMeta(t *testing.T, runDir string) pyval.Obj {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(runDir, "metadata.json"))
	if err != nil {
		t.Fatal(err)
	}
	v, err := pyval.LoadsOrdered(string(raw))
	if err != nil {
		t.Fatalf("metadata.json does not parse: %v\n%s", err, raw)
	}
	obj, ok := v.(pyval.Obj)
	if !ok {
		t.Fatalf("metadata.json is not an object: %s", raw)
	}
	return obj
}

// A FIRST stamp has no prior verdict, and must not invent one. The
// refine note is composed from the value already in metadata.json, and
// `pyval.Str` over an absent key returns the four-character string
// "None" — so a port that reaches for it appends " [refines: None]" to
// the evidence of every run's first stop verdict, which then travels into
// a ledger row as the record of what it superseded.
//
// The seed used by the byte-for-byte differential above always carries a
// stop_verdict, so this case needs a run that has never been stamped.
func TestAFirstStampHasNothingToRefine(t *testing.T) {
	goWS, pyWS := t.TempDir(), t.TempDir()
	const handleID = "20260824T090000-firstone"
	goRD, err := Create(goWS, handleID, "audit the escalation lane")
	if err != nil {
		t.Fatal(err)
	}
	pyRD, err := Create(pyWS, handleID, "audit the escalation lane")
	if err != nil {
		t.Fatal(err)
	}
	seedBytes, err := os.ReadFile(filepath.Join(goRD, "metadata.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(pyRD, "metadata.json"), seedBytes, 0o644); err != nil {
		t.Fatal(err)
	}

	var out string
	if _, err := StampRunStopVerdict(StopTupleOptions{
		StopVerdict:  "thesis-refuted",
		StopEvidence: "converged on a refutation",
		RunDir:       goRD,
		RefineNote:   true,
		EvidenceOut:  &out,
	}); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(out, "refines") {
		t.Errorf("the first stamp recorded itself as refining something: %q", out)
	}

	calls := []stampCall{{
		StopVerdict: "thesis-refuted", StopEvidence: "converged on a refutation",
		RefineNote: true,
	}}
	arg, err := json.Marshal([]any{pyRD, calls})
	if err != nil {
		t.Fatal(err)
	}
	want := runPyIn(t, pyWS, pyStampSrc, string(arg))
	raw, err := os.ReadFile(filepath.Join(goRD, "metadata.json"))
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != want {
		t.Errorf("metadata.json is not CPython's:\n--- go ---\n%s\n--- py ---\n%s\n%s",
			raw, want, firstDiff(string(raw), want))
	}
}
