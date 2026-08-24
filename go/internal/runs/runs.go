// Package runs ports the run-dir slice of runs.py the Go loop needs:
// create the per-run directory, seed and merge metadata.json, and stamp
// the closure verdict tuple. This is the WRITER half of the contract
// recall.FindPriorAttempts already reads — until now a pure-Go
// workspace degraded to zero prior attempts because nothing on this
// side wrote run metadata (named in PORT.md since the recall tranche).
//
// Deliberately unported (see PORT.md): nicknames, the run-ref index,
// thread brains, dispatch-envelope/attachment landing, the stranded-run
// sweep, and cross-process locking on metadata.json (locked_rmw) — the
// Go v0 loop is metadata.json's only writer in its workspace; writes
// are atomic (temp+rename) so concurrent READERS never see a torn file,
// and the single-writer assumption is named here rather than silently
// relied on.
package runs

import (
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"

	"github.com/slycrel/maro-orchestration/go/internal/scrub"
)

// Dir is the run-dir path for a handle id (does not create it). Python
// appends a human nickname (`<id>-<nickname>`); the Go dir is the bare
// id — readers glob runs/*/metadata.json, so naming is not contract.
func Dir(workspaceDir, handleID string) string {
	return filepath.Join(workspaceDir, "runs", handleID)
}

// Create makes the run-dir with the source/build/artifact skeleton
// (the compile mental model — pre-creating makes "where does this go?"
// obvious mid-run), seeds source/prompt.txt first-wins with the
// verbatim goal, and writes the initial metadata. Idempotent.
func Create(workspaceDir, handleID, prompt string) (string, error) {
	rd := Dir(workspaceDir, handleID)
	for _, sub := range []string{"source", "build", "artifact"} {
		if err := os.MkdirAll(filepath.Join(rd, sub), 0o755); err != nil {
			return "", err
		}
	}
	promptPath := filepath.Join(rd, "source", "prompt.txt")
	if _, err := os.Stat(promptPath); os.IsNotExist(err) {
		if werr := atomicWrite(promptPath, []byte(prompt)); werr != nil {
			return "", werr
		}
	}
	// The order is write_metadata's, for the subset this port writes:
	// handle_id, [nickname], prompt, [lane], [model], started_at,
	// [ended_at], status, pid. started_at is passed explicitly rather
	// than left to WriteMetadata's fill so it lands in ITS position and
	// not at the tail.
	if err := WriteMetadata(rd, pyval.Obj{
		{Key: "handle_id", Val: handleID},
		{Key: "prompt", Val: prompt},
		{Key: "started_at", Val: time.Now().UTC().Format(time.RFC3339)},
		{Key: "status", Val: "running"},
		{Key: "pid", Val: os.Getpid()},
	}); err != nil {
		return "", err
	}
	return rd, nil
}

// WriteMetadata merges fields into metadata.json. started_at is set
// once and preserved thereafter (Python write_metadata parity); an
// existing key survives unless the new fields name it; a nil value
// POPS its key — the tri-state carrier goal_achieved and friends need
// delete semantics, and "set to null" would read as a judged false by
// sloppy consumers. The write is atomic so readers (FindPriorAttempts
// in either runtime) never see a torn file.
func WriteMetadata(runDir string, fields pyval.Obj) error {
	metaPath := filepath.Join(runDir, "metadata.json")
	var existing pyval.Obj
	if raw, err := os.ReadFile(metaPath); err == nil {
		// A corrupt existing file degrades to a fresh object — the merge
		// must not wedge the run on a torn predecessor. LoadsOrdered,
		// not json.Unmarshal: a merge that rewrites someone else's file
		// has to give the keys back in the order they arrived, and a Go
		// map cannot (see the pyval.Obj doc).
		if v, perr := pyval.LoadsOrdered(string(raw)); perr == nil {
			if o, ok := v.(pyval.Obj); ok {
				existing = o
			}
		}
	}
	for _, f := range fields {
		if f.Key == "started_at" {
			if _, ok := existing.Get("started_at"); ok {
				continue // first writer wins, like Python
			}
		}
		if f.Val == nil {
			existing.Pop(f.Key)
			continue
		}
		existing.Set(f.Key, f.Val)
	}
	if _, ok := existing.Get("started_at"); !ok {
		existing.Set("started_at", time.Now().UTC().Format(time.RFC3339))
	}
	// pyval.DumpsIndent2, not json.MarshalIndent: Python writes this file
	// with json.dumps(meta, indent=2), and encoding/json differs from it
	// three ways at once — sorted keys (handled above), HTML-escaping
	// `<`, `>` and `&`, and raw UTF-8 where json.dumps defaults to
	// ensure_ascii. The prompt lands in here verbatim, so any goal
	// containing "->" or a non-ASCII character produced a metadata.json
	// no CPython writer would have produced (adversarial mission-r7
	// HIGH).
	out, err := pyval.DumpsIndent2(existing)
	if err != nil {
		return err
	}
	return atomicWrite(metaPath, []byte(out))
}

// Finalize stamps the run's terminal status and ended_at.
func Finalize(runDir, status string) error {
	// ended_at before status: write_metadata's order.
	return WriteMetadata(runDir, pyval.Obj{
		{Key: "ended_at", Val: time.Now().UTC().Format(time.RFC3339)},
		{Key: "status", Val: status},
	})
}

// StampVerdict is THE verdict-tuple replacement, ported from
// _apply_verdict_tuple: one implementation for every verdict writer
// (Python round-15 review: a second hand-maintained field list had
// already drifted). Every member is set or popped; nothing is left to
// a merge. goalAchieved == nil pops the boolean — an UNJUDGED verdict
// stamps nothing, because absence means "not judged" and a false here
// demotes the run everywhere the stamp is read.
func StampVerdict(runDir string, goalAchieved *bool, source, summary string,
	confidence *float64, downgradeReason string, gaps []string) error {
	// _apply_verdict_tuple's assignment order: source, summary,
	// confidence, downgrade_reason, gaps, goal_achieved. A popped member
	// is a nil Val, which WriteMetadata deletes.
	fields := pyval.Obj{
		{Key: "goal_verdict_source", Val: source},
		{Key: "goal_verdict_summary", Val: budget.VerdictProse.Clip(summary)},
	}
	// confidence == nil pops the key (Python _apply_verdict_tuple:
	// "confidence=None pops the key — the NOW lane records no
	// confidence"). A fabricated 0 on a judged-true NOW verdict would
	// read as "verified with zero confidence" to any confidence-weighted
	// consumer — the opposite of what happened (adversarial routing r1
	// 2026-08-22, Expert QA).
	if confidence == nil {
		fields.Set("goal_verdict_confidence", nil)
	} else {
		fields.Set("goal_verdict_confidence", *confidence)
	}
	if downgradeReason != "" {
		fields.Set("goal_verdict_downgrade_reason", budget.VerdictProse.Clip(downgradeReason))
	} else {
		fields.Set("goal_verdict_downgrade_reason", nil)
	}
	var kept []string
	for _, g := range gaps {
		if g != "" {
			kept = append(kept, budget.Clip(g, 500))
		}
	}
	if len(kept) > 5 {
		// Count cuts announce themselves like char cuts do (Python
		// round-14 review: five-of-seven gaps rendered as complete).
		extra := len(kept) - 5
		kept = append(kept[:5], fmt.Sprintf("(+%d more gap(s) in the closure verdict artifact)", extra))
	}
	if len(kept) > 0 {
		fields.Set("goal_verdict_gaps", kept)
	} else {
		fields.Set("goal_verdict_gaps", nil)
	}
	if goalAchieved == nil {
		fields.Set("goal_achieved", nil)
	} else {
		fields.Set("goal_achieved", *goalAchieved)
	}
	return WriteMetadata(runDir, fields)
}

// AppendVerdictRow appends one closure outcome row to the run's durable
// build/closure_verdicts.jsonl (persist-the-artifacts decree, Jeremy
// 2026-07-29: every closure outcome — full verdict or named skip —
// leaves a row). Append-only, one JSON object per line.
func AppendVerdictRow(runDir, loopID string, row pyval.Obj) error {
	// loop_id is Python's second key and was MISSING from every Go
	// verdict row. _persist_verdict_row closes over `loop_id` and writes
	// `loop_id or ""`; closure.Options.LoopID was set by the loop and
	// read by nothing, so a restarted run's parent and child verdicts
	// landed in one append-only file with no way to tell them apart —
	// and that join is the §9.3 material the comment below calls the
	// reason this file exists (adversarial mission-r7 HIGH).
	full := pyval.Obj{
		{Key: "ts", Val: time.Now().UTC().Format(time.RFC3339)},
		{Key: "loop_id", Val: loopID},
	}
	for _, f := range row {
		full.Set(f.Key, f.Val)
	}
	// Scrub at the single write owner (Python: `scrub({...**row})` at
	// closure_verify.py's _persist_verdict_row) — probe stdout/stderr and
	// LLM prose land verbatim in a durable jsonl otherwise, and no
	// downstream pass ever rescrubs this file (adversarial closure r1
	// 2026-08-22, Skeptic HIGH: a working scrub package existed and the
	// one new durable sink didn't call it). The JSON round-trip first is
	// load-bearing, not belt-and-braces: scrub.Walk descends only
	// []any/map[string]any, and callers hand this function concretely
	// typed nests ([]map[string]any check rows) it would otherwise skip
	// wholesale — the scrub pin test caught exactly that.
	// FromPlain, not a json.Marshal/Unmarshal round-trip. The round-trip
	// was there so scrub.Walk would descend the concretely-typed nests
	// callers hand this function ([]map[string]any check rows) instead
	// of skipping them wholesale — the scrub pin test caught exactly
	// that. FromPlain does the same widening without going through
	// encoding/json, which matters because json.Marshal(3.0) is "3":
	// the round-trip handed back an int where json.dumps writes "3.0",
	// and confidence is a float that is often whole.
	norm := pyval.FromPlain(full)
	// DumpsCompactPy, not json.Marshal: Python's bare json.dumps carries
	// `", "` and `": "` separators, does NOT HTML-escape, and DOES
	// \uXXXX-escape non-ASCII. FailedCheckSignature is "%s => exit %d:
	// %s", so EVERY Go failed-check entry on this rail carried
	// `=\u003e` where CPython writes `=>` (adversarial mission-r7 HIGH).
	out, err := pyval.DumpsCompactPy(scrub.Walk(norm, scrub.Secrets))
	if err != nil {
		return err
	}
	f, err := os.OpenFile(filepath.Join(runDir, "build", "closure_verdicts.jsonl"),
		os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	if _, err := f.Write([]byte(out + "\n")); err != nil {
		return err
	}
	return f.Sync()
}

func atomicWrite(path string, data []byte) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// SkillManifestEntry is one skill that actually entered a prompt. The shape
// is the caller's — this stays a dumb appender.
type SkillManifestEntry struct {
	ID          string  `json:"id"`
	Name        string  `json:"name"`
	ContentHash string  `json:"content_hash"`
	VariantOf   *string `json:"variant_of"`
	Tier        string  `json:"tier"`
	RoutingKey  string  `json:"routing_key"`
	MatchMethod string  `json:"match_method"`
	MatchScore  float64 `json:"match_score"`
}

// SkillManifestMeta is the record-level selection telemetry: present even
// when the skills list is empty, which is what turns the binary gap signal
// ("nothing matched") into a graded one.
type SkillManifestMeta struct {
	Method      string  `json:"method"`
	NCandidates int     `json:"n_candidates"`
	TopScore    float64 `json:"top_score"`
}

// AppendSkillsManifest appends one skill-injection event to
// <run-dir>/source/skills_manifest.jsonl.
//
// An EMPTY entries list is RECORDED, not skipped. Absence of this file used
// to mean two different things — "no skills matched" and "the recorder never
// ran" — which makes the file useless as an attribution rail: a cold-store
// run legitimately matches nothing, and that is a data point, not a gap.
// Present-and-empty means nothing was injected; absent means the recorder
// genuinely did not run.
//
// JSONL because injection can happen more than once per run (decompose,
// curated summaries, replans). Best-effort by contract: it returns an error
// for the caller's warning channel but a failure must never kill a run.
func AppendSkillsManifest(runDir string, entries []SkillManifestEntry,
	stage string, meta *SkillManifestMeta, now string) error {
	if runDir == "" {
		return nil
	}
	if entries == nil {
		entries = []SkillManifestEntry{}
	}
	// Every field is rendered through pyjson, entries included. Go structs
	// reach the generic encoder, which spells a whole float without its
	// ".0" — so a match_score of 2.0 landed as 2 next to Python's 2.0 on a
	// file whose entire job is cross-runtime attribution.
	items := make([]map[string]any, 0, len(entries))
	for _, e := range entries {
		var variant any
		if e.VariantOf != nil {
			variant = *e.VariantOf
		}
		items = append(items, map[string]any{
			"id": e.ID, "name": e.Name, "content_hash": e.ContentHash,
			"variant_of": variant, "tier": e.Tier, "routing_key": e.RoutingKey,
			"match_method": e.MatchMethod, "match_score": e.MatchScore,
		})
	}
	skills, err := pyjson.Array(items, []string{"id", "name", "content_hash",
		"variant_of", "tier", "routing_key", "match_method", "match_score"})
	if err != nil {
		return err
	}
	rec := map[string]any{"ts": now, "stage": stage, "skills": skills}
	if meta != nil {
		m, err := pyjson.Ordered(map[string]any{"method": meta.Method,
			"n_candidates": meta.NCandidates, "top_score": meta.TopScore},
			[]string{"method", "n_candidates", "top_score"})
		if err != nil {
			return err
		}
		rec["match"] = pyjson.Raw(m)
	}
	// Key ORDER and HTML escaping are set explicitly, the way every other
	// emitter on this rail does. Plain json.Marshal sorts map keys
	// alphabetically and escapes < > & as \u003c-style sequences, so this
	// one writer produced "match" before "stage" and turned a skill named
	// `Report <x>` into `Report \u003cx\u003e` — values identical, bytes
	// not, on a file whose whole job is cross-runtime attribution.
	raw, err := pyjson.Ordered(rec, []string{"ts", "stage", "skills", "match"})
	if err != nil {
		return err
	}
	out := filepath.Join(runDir, "source", "skills_manifest.jsonl")
	if err := os.MkdirAll(filepath.Dir(out), 0o755); err != nil {
		return err
	}
	return record.AppendRawLine(out, []byte(raw))
}
