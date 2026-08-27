// Import — trust demotion per PORTABLE_LEARNING_DESIGN §3 ("imports are
// contested-by-birth"). Ports pack.import_pack.
//
// Arrival-trust table, same as Python's:
//   - standing rules  → Hypothesis, confirmations/contradictions reset 0
//   - hypotheses      → re-demoted the same way (contested-by-birth is
//     uniform, not just the rules path)
//   - lessons         → MEDIUM tier, score capped 0.5, counters reset,
//     provenance gate re-applied (transport must not launder a
//     quarantined lesson into an injectable one)
//   - skills/personas .md → never live; quarantined to imports/<label>/
//   - knowledge/playbook/runs → quarantine-only
//   - unknown classes → quarantined under unknown/ (§6 forward-compat)
//
// v1 divergence from Python, loud and named: skill_records quarantine to
// imports/<label>/ instead of importing natively — this runtime has no
// skills store yet (tools tranche). The rows are preserved verbatim; the
// outcome string says exactly what happened.
package pack

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/knowledge"
	"github.com/slycrel/maro-orchestration/go/internal/provenance"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

var quarantineOnlyClasses = map[string]bool{
	"knowledge_nodes": true, "knowledge_edges": true,
	"playbook": true, "run_artifact": true,
}

// ImportOpts mirrors import_pack's surface.
type ImportOpts struct {
	PackPath        string
	Label           string
	Target          string // resolved workspace (live-store discipline)
	AllowUnreviewed bool
	DryRun          bool
}

// ImportReport carries the same keys as Python's report dict so the audit
// rows are cross-runtime comparable.
//
// The rows are pyval.Obj, not map[string]any: every one of them is a
// Python dict LITERAL, and this whole report is json.dumps'd into
// memory/imports.jsonl — a shared audit ledger both runtimes append to
// and a human greps. A sorted row is a different stored value from an
// insertion-order one even when every key and value agrees.
type ImportReport struct {
	Pack                     string      `json:"pack"`
	PackTag                  string      `json:"pack_tag"`
	Label                    string      `json:"label"`
	ImportedAt               string      `json:"imported_at"`
	DryRun                   bool        `json:"dry_run"`
	RulesDemotedToHypotheses []pyval.Obj `json:"rules_demoted_to_hypotheses"`
	HypothesesImported       []pyval.Obj `json:"hypotheses_imported"`
	LessonsImported          []pyval.Obj `json:"lessons_imported"`
	SkillRecordsImported     []pyval.Obj `json:"skill_records_imported"`
	SkillsMD                 []pyval.Obj `json:"skills_md"`
	PersonasMD               []pyval.Obj `json:"personas_md"`
	Quarantined              []pyval.Obj `json:"quarantined"`
	QuarantinedUnknown       []pyval.Obj `json:"quarantined_unknown"`
}

// malformedRow is the per-row fault row every trust lane writes, in
// CPython's order: the id field, then outcome, then error (pack.py:578,
// 635, 852). It is one helper rather than eight literals because the
// order is the same at every site and a hand-copied literal is how one
// of them drifts.
func malformedRow(idField, idValue string, err error) pyval.Obj {
	return pyval.Obj{
		{Key: idField, Val: idValue},
		{Key: "outcome", Val: "malformed_skipped"},
		{Key: "error", Val: err.Error()},
	}
}

// withClassFirst is Python's `{"class": cls, **rest}` — `class` takes the
// FIRST ordinal and the spread follows, so a quarantine row reads
// {class, path, outcome}. Set, not append, so a `class` already inside
// rest is overwritten in place exactly as the spread would.
func withClassFirst(cls string, rest pyval.Obj) pyval.Obj {
	out := pyval.Obj{{Key: "class", Val: cls}}
	for _, f := range rest {
		out.Set(f.Key, f.Val)
	}
	return out
}

// safeLabel ports _safe_label.
func safeLabel(label string) error {
	if label == "" || strings.HasPrefix(label, "/") || strings.Contains(label, "/") {
		return fmt.Errorf("import refused: unsafe label: %q", label)
	}
	for _, part := range strings.Split(label, "/") {
		if part == ".." {
			return fmt.Errorf("import refused: unsafe label: %q", label)
		}
	}
	if label == ".." {
		return fmt.Errorf("import refused: unsafe label: %q", label)
	}
	return nil
}

// safeRelpath ports _safe_relpath — the manifest is untrusted input; a
// sealed pack only proves a human read REVIEW.md, not that pack.json's
// paths are well-formed. Validate at the boundary.
func safeRelpath(relpath, what string) error {
	if relpath == "" || strings.HasPrefix(relpath, "/") {
		return fmt.Errorf("import refused: unsafe %s path in manifest: %q", what, relpath)
	}
	for _, part := range strings.Split(relpath, "/") {
		if part == ".." {
			return fmt.Errorf("import refused: unsafe %s path in manifest: %q", what, relpath)
		}
	}
	return nil
}

func artifactRelpath(a pyval.Obj) (string, error) {
	rel := strings.TrimPrefix(a.GetString("path"), "artifacts/")
	if err := safeRelpath(rel, "artifact"); err != nil {
		return "", err
	}
	return rel, nil
}

// Import verifies, demotes, quarantines, and audits. Refuses unsealed
// packs (AllowUnreviewed is the self-to-self escape hatch) and newer pack
// formats outright — never best-effort a format we don't understand on
// trust-bearing data (§6).
func Import(opts ImportOpts) (*ImportReport, error) {
	if _, err := os.Stat(opts.PackPath); err != nil {
		return nil, fmt.Errorf("no such pack: %s", opts.PackPath)
	}
	if err := safeLabel(opts.Label); err != nil {
		return nil, err
	}
	ws := opts.Target
	if ws == "" {
		return nil, fmt.Errorf("import: workspace not resolved by caller")
	}

	members, err := readArchive(opts.PackPath)
	if err != nil {
		return nil, err
	}
	manifestRaw, ok := members["pack.json"]
	if !ok {
		return nil, fmt.Errorf("import refused: %s has no pack.json", opts.PackPath)
	}
	manifest, err := decodeManifest(manifestRaw)
	if err != nil {
		return nil, err
	}
	// Fail CLOSED on a pack_format we can't even read as an integer — a
	// string "99", a float, an array all refused, not silently skipped
	// (adversarial round 2026-08-22, Minimalist HIGH: the type assertion
	// alone let type-confused values bypass the version gate entirely.
	// Python's `fmt > PACK_FORMAT` TypeErrors on the same input — an ugly
	// crash, but closed).
	if raw, present := manifest.Get("pack_format"); present {
		num, ok := raw.(json.Number)
		if !ok {
			return nil, fmt.Errorf("import refused: pack_format is not a number: %v", raw)
		}
		v, err := num.Int64()
		if err != nil || v < 0 {
			return nil, fmt.Errorf("import refused: pack_format is not a valid integer: %v", raw)
		}
		if v > PackFormat {
			return nil, fmt.Errorf("pack format %d > supported %d — upgrade maro", v, PackFormat)
		}
	}

	reviewRaw, _ := manifest.Get("review")
	review, _ := reviewRaw.(pyval.Obj)
	reviewHuman, _ := review.Get("human_reviewed")
	humanReviewed, _ := reviewHuman.(bool)
	if !humanReviewed && !opts.AllowUnreviewed {
		return nil, fmt.Errorf(
			"import refused: pack is not sealed (review.human_reviewed=false) — " +
				"read REVIEW.md and seal it, or pass --allow-unreviewed for a " +
				"self-to-self transfer")
	}

	archivedReview := string(members["REVIEW.md"])
	artifacts := manifestArtifacts(manifest)
	artifactBytes := map[string][]byte{}
	for _, a := range artifacts {
		p := a.GetString("path")
		if p == "pack.json" || p == "REVIEW.md" {
			// Seal excludes the reserved members before its bijection loop,
			// so it refuses these; Import read them from the raw member map
			// and would have fed the manifest/review bytes through a trust
			// lane (r2 2026-08-22, QA — keep the two checks provably the
			// same, not incidentally so).
			return nil, fmt.Errorf(
				"import refused: manifest lists reserved member %q as an artifact", p)
		}
		data, present := members[p]
		if !present {
			return nil, fmt.Errorf(
				"import refused: manifest names %q but the archive has no such member", p)
		}
		if _, dup := artifactBytes[p]; dup {
			// A path listed twice (e.g. under two classes) would run one
			// reviewed artifact through two trust lanes while the payload
			// digest sees it once. Go-only hardening; Python shares the
			// gap (named in PORT.md).
			return nil, fmt.Errorf(
				"import refused: manifest lists %q more than once", p)
		}
		artifactBytes[p] = data
	}
	// Bijection, the other direction: an archive member the manifest never
	// mentions rides outside the digest and outside REVIEW.md. Refuse —
	// neither runtime's exporter ever produces one.
	for name := range members {
		if name == "pack.json" || name == "REVIEW.md" {
			continue
		}
		if _, listed := artifactBytes[name]; !listed {
			return nil, fmt.Errorf(
				"import refused: archive member %q is not listed in the manifest", name)
		}
	}

	if humanReviewed {
		expected := review.GetString("review_manifest_sha256")
		if expected == "" || sha256Text(archivedReview) != expected {
			return nil, fmt.Errorf(
				"import refused: REVIEW.md in the archive does not match the sealed " +
					"hash — possible post-seal tampering")
		}
		expectedPayload := review.GetString("review_payload_sha256")
		marker := fmt.Sprintf("Reviewed payload SHA-256: `%s`", expectedPayload)
		actualPayload, err := payloadSHA256(artifacts, artifactBytes)
		if err != nil {
			return nil, err
		}
		if expectedPayload == "" || !strings.Contains(archivedReview, marker) ||
			actualPayload != expectedPayload {
			return nil, fmt.Errorf(
				"import refused: archived artifacts do not match the payload digest " +
					"embedded in the human-reviewed REVIEW.md")
		}
	}

	// Independently verify every artifact's declared sha256. Fail closed on
	// missing or mismatched hashes even for --allow-unreviewed imports.
	for _, a := range artifacts {
		p := a.GetString("path")
		declared := a.GetString("sha256")
		if declared == "" || sha256Text(string(artifactBytes[p])) != declared {
			return nil, fmt.Errorf(
				"import refused: artifact %q does not match its manifest sha256 — "+
					"possible post-seal tampering", p)
		}
	}

	packName := manifest.GetString("name")
	if packName == "" {
		packName = strings.TrimSuffix(filepath.Base(opts.PackPath), ArchiveSuffix)
	}
	fileSHA, err := sha256File(opts.PackPath)
	if err != nil {
		return nil, err
	}
	packTag := fmt.Sprintf("%s@%s", packName, fileSHA[:8])
	now := nowISO()

	report := &ImportReport{
		Pack: packName, PackTag: packTag, Label: opts.Label,
		ImportedAt: now, DryRun: opts.DryRun,
		RulesDemotedToHypotheses: []pyval.Obj{},
		HypothesesImported:       []pyval.Obj{},
		LessonsImported:          []pyval.Obj{},
		SkillRecordsImported:     []pyval.Obj{},
		SkillsMD:                 []pyval.Obj{},
		PersonasMD:               []pyval.Obj{},
		Quarantined:              []pyval.Obj{},
		QuarantinedUnknown:       []pyval.Obj{},
	}

	store := knowledge.NewStore(ws)
	// The provenance killswitch reads the AMBIENT config (user tier +
	// env-resolved workspace), exactly like Python's provenance_gate_enabled
	// — an explicit -target does not move which config is consulted.
	cfg, _ := config.Load()
	imp := &importer{
		ws: ws, store: store, packName: packName, label: opts.Label,
		packTag: packTag, now: now, dryRun: opts.DryRun,
		provGate: provenance.GateEnabled(
			config.GetRaw(cfg, "knowledge.provenance_gate_enabled", true)),
	}

	// The per-target gate makes each import's load/check/write decisions one
	// transaction from the importer's perspective; individual stores retain
	// their own locks for coordination with non-import writers.
	gate := filepath.Join(ws, "memory", ".pack-import")
	if err := os.MkdirAll(filepath.Dir(gate), 0o755); err != nil {
		return nil, err
	}
	err = record.Locked(gate, func() error {
		for _, artifact := range artifacts {
			cls := artifact.GetString("class")
			rel, err := artifactRelpath(artifact)
			if err != nil {
				return err
			}
			content := string(artifactBytes[artifact.GetString("path")])
			switch {
			case cls == "rules":
				rows, err := imp.importRulesAsHypotheses(content)
				if err != nil {
					return err
				}
				report.RulesDemotedToHypotheses = append(report.RulesDemotedToHypotheses, rows...)
			case cls == "hypotheses":
				rows, err := imp.importHypotheses(content)
				if err != nil {
					return err
				}
				report.HypothesesImported = append(report.HypothesesImported, rows...)
			case cls == "lessons" || cls == "lessons_medium":
				rows, err := imp.importLessons(content)
				if err != nil {
					return err
				}
				report.LessonsImported = append(report.LessonsImported, rows...)
			case cls == "skill_records":
				// v1 divergence (named): no native skills store in this
				// runtime yet — preserve the rows in quarantine rather than
				// half-import them. The outcome string is the honesty.
				res, err := imp.quarantineSingle(rel, content)
				if err != nil {
					return err
				}
				// The outcome is REPLACED in place — position two, where
				// _quarantine_single put it — and `class` takes the lead,
				// so this row reads like the quarantine rows it is a
				// sibling of. There is no CPython counterpart to copy an
				// order from (CPython imports these natively), so the
				// order is borrowed from the lane this row belongs to
				// rather than invented.
				res.Set("outcome", "quarantined_no_native_skill_store_v1")
				report.SkillRecordsImported = append(
					report.SkillRecordsImported, withClassFirst(cls, res))
			case cls == "skill_md":
				res, err := imp.importAuthoredMD("skills", rel, content)
				if err != nil {
					return err
				}
				report.SkillsMD = append(report.SkillsMD, res)
			case cls == "persona_md":
				res, err := imp.importAuthoredMD("personas", rel, content)
				if err != nil {
					return err
				}
				report.PersonasMD = append(report.PersonasMD, res)
			case quarantineOnlyClasses[cls]:
				res, err := imp.quarantineSingle(rel, content)
				if err != nil {
					return err
				}
				// pack.py:1144-1145 — `{"class": cls, **_quarantine_single(...)}`.
				report.Quarantined = append(report.Quarantined, withClassFirst(cls, res))
			default:
				res, err := imp.quarantineSingle("unknown/"+rel, content)
				if err != nil {
					return err
				}
				// pack.py:1147-1148 — the same spread, unknown/ prefixed.
				report.QuarantinedUnknown = append(
					report.QuarantinedUnknown, withClassFirst(cls, res))
			}
		}
		if !opts.DryRun {
			// pack.py:1154 — json.dumps of the report with action spread
			// in last. Same shared audit trail as adopt (mission-r8).
			line, err := pyval.DumpsStruct(struct {
				*ImportReport
				Action string `json:"action"`
			}{report, "pack_import"})
			if err != nil {
				return err
			}
			raw := []byte(line)
			audit := filepath.Join(ws, "memory", "imports.jsonl")
			return record.AppendRawLine(audit, raw)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return report, nil
}

type importer struct {
	ws       string
	store    *knowledge.Store
	packName string
	label    string
	packTag  string
	now      string
	dryRun   bool
	provGate bool // knowledge.provenance_gate_enabled (default true)
}

// rowID extracts the identity field of one imported row. Scalars are
// coerced to Python's str() form (Python builds the new id with an
// f-string, so a numeric or bool id imports there — r2 2026-08-22
// narrowed r1's refuse-all-non-strings, which silently dropped rows a
// Python import keeps). Absent, null, empty, and composite (array/
// object) ids are refused: with the "" fallback every such row collapses
// onto the SAME "imported-<pack>-" identity — the first imports, the
// rest are silently eaten as "already_imported" (r1, Skeptic; Python
// still has the collapse for absent/empty and stringifies composites,
// named in PORT.md). The second return is the raw value for the report
// row, so the audit trail keeps what the row actually carried.
// The second return is the offending value rendered as a string for the
// report row (always a string — the report field's type contract holds
// across every outcome; r3 2026-08-22, QA).
func rowID(row map[string]any, field string) (string, string, error) {
	v, present := row[field]
	if !present {
		return "", "", fmt.Errorf("%s missing", field)
	}
	switch v.(type) {
	case nil:
		return "", "", fmt.Errorf("%s is null", field)
	case string, json.Number, float64, bool:
		if s := asString(v); s != "" {
			return s, s, nil
		}
		return "", "", fmt.Errorf("%s is empty", field)
	default:
		return "", fmt.Sprintf("%v", v), fmt.Errorf("%s is not a scalar: %v", field, v)
	}
}

// scannedRow is one JSONL line in FILE ORDER: either a decoded row or
// the reason it was refused before decoding. Single-pass so the report
// order matches the artifact's line order, like Python's one loop
// (r3 2026-08-22, Skeptic: the r2 callback shape emitted every
// malformed row before any successful one).
type scannedRow struct {
	row map[string]any
	err error
}

// scanRows splits one JSONL artifact into rows, refusing rows whose raw
// text carries lone-surrogate \u escapes BEFORE decoding — Go's decoder
// would silently rewrite them to U+FFFD inside the exact trust-bearing
// text the provenance classifier reads, where Python keeps (and later
// chokes loudly on) the lone surrogate (r2 2026-08-22, Skeptic HIGH).
// Decoding uses UseNumber, like decodeManifest, so numeric ids keep
// their source literal exactly (r3, both lenses: a plain Unmarshal made
// rowID's json.Number branch dead code and rounded >2^53 ids through
// float64 — divergent identities and a craftable id collision).
// A refused row costs that row, not the import; undecodable rows skip
// silently to match Python's json.JSONDecodeError → continue.
func scanRows(content string) []scannedRow {
	var out []scannedRow
	// `[ln for ln in content.splitlines() if ln.strip()]` — the same pair the
	// exporter's _read_jsonl_rows needed, unfixed at the READER for all three
	// trust lanes. U+001F is the case that makes it asymmetric rather than
	// merely different: splitlines() does NOT break on it, so a row prefixed
	// with one survives a CPython export intact as a single member line, and
	// then strings.TrimSpace — which does not know U+001C-U+001F — leaves it
	// on the front, decodeStrictJSONObject refuses the row, and the import
	// drops it SILENTLY. Measured on a real CPython-sealed pack: 1 lesson
	// imported there, zero here, and not even a malformed_skipped row to say
	// so.
	for _, line := range pytext.SplitLines(content) {
		line = pytext.Strip(line)
		if line == "" {
			continue
		}
		if err := refuseLoneSurrogates([]byte(line)); err != nil {
			out = append(out, scannedRow{err: err})
			continue
		}
		row, err := decodeStrictJSONObject(line)
		if err != nil {
			continue
		}
		out = append(out, scannedRow{row: row})
	}
	return out
}

// provenanceStamp is the rules/hypotheses lanes' `imported` block, in
// Python's INSERTION order — a dict literal there, so json.dumps writes it
// in the order it is written, and this block lands in hypotheses.jsonl,
// which is a live shared store rather than an audit trail. A Go map has no
// order and DumpsStruct sorted it, so the two runtimes wrote different bytes
// for the same provenance chain.
func (im *importer) provenanceStamp(originalID, originalClass string, row map[string]any) pyval.Obj {
	imported := pyval.Obj{
		{Key: "imported_from", Val: im.label},
		{Key: "pack", Val: im.packTag},
		{Key: "original_id", Val: originalID},
		{Key: "original_class", Val: originalClass},
		{Key: "original_confirmations", Val: row["confirmations"]},
		{Key: "original_contradictions", Val: row["contradictions"]},
		{Key: "imported_at", Val: im.now},
	}
	// `if row.get("imported"): imported["original_provenance"] = row["imported"]`
	// — a truthiness test, and the value is stored WHATEVER IT IS.
	//
	// The type assertion made this a shape test that only dicts pass, so an
	// incoming `"imported": "from-elsewhere"` dropped the provenance chain
	// here and kept it there. This file already fixes exactly this Python
	// idiom twice, one field apart (CoerceEvidenceSources, Truthy on
	// provisional) — lens 3 again: a fix is evidence about its siblings, and
	// the third instance was one screen away from both of them.
	if pyval.Truthy(row["imported"]) {
		// FromPlain, because the value came off the strict decoder as a Go
		// container and the ordered writer refuses those by construction.
		//
		// NAMED RESIDUAL, and it is the decoder's not this line's: an
		// incoming provenance OBJECT arrives as a map[string]any with its
		// order already gone, so FromPlain sorts it where CPython keeps the
		// parse order. Scalars and arrays — which is what a chain actually
		// carries — are unaffected. The fix is the same one tiered.go's
		// loader needs: decode this path through pyval.LoadsOrdered.
		imported.Set("original_provenance", pyval.FromPlain(row["imported"]))
	}
	return imported
}

// importRulesAsHypotheses: standing rules demote to Hypothesis on arrival.
func (im *importer) importRulesAsHypotheses(content string) ([]pyval.Obj, error) {
	snap, err := im.store.HypothesisSnapshot()
	if err != nil {
		return nil, err
	}
	var results []pyval.Obj
	for _, sr := range scanRows(content) {
		if sr.err != nil {
			results = append(results, malformedRow("rule_id", "", sr.err))
			continue
		}
		row := sr.row
		originalID, rawID, err := rowID(row, "rule_id")
		if err != nil {
			results = append(results, malformedRow("rule_id", rawID, err))
			continue
		}
		ruleText, _ := row["rule"].(string)
		hypID := fmt.Sprintf("imported-%s-%s", im.packName, originalID)
		if snap.IDs[hypID] {
			results = append(results, pyval.Obj{
				{Key: "rule_id", Val: originalID},
				{Key: "outcome", Val: "already_imported"}})
			continue
		}
		if ruleText != "" && snap.Texts[ruleText] {
			results = append(results, pyval.Obj{
				{Key: "rule_id", Val: originalID},
				{Key: "outcome", Val: "skipped_identical"}})
			continue
		}
		domain, _ := row["domain"].(string)
		hyp := knowledge.Hypothesis{
			HypID: hypID, Lesson: ruleText, Domain: domain,
			Confirmations: 0, Contradictions: 0,
			SourceLessonIDs: []string{fmt.Sprintf("imported:%s/%s", im.packName, originalID)},
			FirstSeen:       im.now, LastSeen: im.now,
			Imported: im.provenanceStamp(originalID, "rules", row),
		}
		if !im.dryRun {
			if err := im.store.AppendHypothesis(hyp); err != nil {
				results = append(results, malformedRow("rule_id", originalID, err))
				continue
			}
		}
		snap.IDs[hypID] = true
		// pack.py:571 — rule_id, hyp_id, outcome.
		results = append(results, pyval.Obj{
			{Key: "rule_id", Val: originalID},
			{Key: "hyp_id", Val: hypID},
			{Key: "outcome", Val: "demoted_to_hypothesis"}})
	}
	return results, nil
}

// importHypotheses: already-hypothesis rows still reset trust on arrival.
func (im *importer) importHypotheses(content string) ([]pyval.Obj, error) {
	snap, err := im.store.HypothesisSnapshot()
	if err != nil {
		return nil, err
	}
	var results []pyval.Obj
	for _, sr := range scanRows(content) {
		if sr.err != nil {
			results = append(results, malformedRow("hyp_id", "", sr.err))
			continue
		}
		row := sr.row
		originalID, rawID, err := rowID(row, "hyp_id")
		if err != nil {
			results = append(results, malformedRow("hyp_id", rawID, err))
			continue
		}
		lessonText, _ := row["lesson"].(string)
		hypID := fmt.Sprintf("imported-%s-%s", im.packName, originalID)
		if snap.IDs[hypID] {
			results = append(results, pyval.Obj{
				{Key: "hyp_id", Val: originalID},
				{Key: "outcome", Val: "already_imported"}})
			continue
		}
		if lessonText != "" && snap.Texts[lessonText] {
			results = append(results, pyval.Obj{
				{Key: "hyp_id", Val: originalID},
				{Key: "outcome", Val: "skipped_identical"}})
			continue
		}
		domain, _ := row["domain"].(string)
		hyp := knowledge.Hypothesis{
			HypID: hypID, Lesson: lessonText, Domain: domain,
			Confirmations: 0, Contradictions: 0,
			SourceLessonIDs: []string{fmt.Sprintf("imported:%s/%s", im.packName, originalID)},
			FirstSeen:       im.now, LastSeen: im.now,
			Imported: im.provenanceStamp(originalID, "hypotheses", row),
		}
		if !im.dryRun {
			if err := im.store.AppendHypothesis(hyp); err != nil {
				results = append(results, malformedRow("hyp_id", originalID, err))
				continue
			}
		}
		snap.IDs[hypID] = true
		// pack.py:633 — hyp_id, new_hyp_id, outcome.
		results = append(results, pyval.Obj{
			{Key: "hyp_id", Val: originalID},
			{Key: "new_hyp_id", Val: hypID},
			{Key: "outcome", Val: "imported"}})
	}
	return results, nil
}

// importVariants ports _import_variants: strings only, local per-variant
// clip and cap — the exporter's bounds are not this box's contract.
func importVariants(raw any) []string {
	arr, ok := raw.([]any)
	if !ok {
		return nil
	}
	var out []string
	for _, v := range arr {
		s, ok := v.(string)
		// `not v.strip()`. NO MUTATION CAN PIN THIS SPELLING and the
		// reason is the fix one frame down: knowledge.AbsorbVariant now
		// strips with pytext.Strip too, so a variant of only separators is
		// dropped there whether or not this gate saw it. Two guards, one
		// observable — which is exactly the shape that hides a wrong bound
		// (a duplicated guard is what makes the wrong one invisible), so
		// the pairing is written down rather than left to be rediscovered
		// the next time one of them is "simplified" away.
		if !ok || pytext.Strip(s) == "" {
			continue
		}
		out = knowledge.AbsorbVariant(out, s, "")
		if len(out) >= knowledge.MergedVariantsCap {
			break
		}
	}
	return out
}

// asFloat ports Python float() coercion: JSON numbers pass; numeric
// strings pass ("0.7"); anything else errors (→ malformed_skipped, per-row
// fault isolation — a junk value costs the import, not the store).
// pyFloatGet is `float(row.get(key, default))`, and the parameter that
// matters is the one Go cannot express as an argument: PRESENCE.
//
// `.get(k, d)` returns d only when the key is ABSENT. An explicit JSON null
// is a present key, so Python gets None, `float(None)` raises TypeError, the
// per-row except catches it and the row is reported malformed_skipped. Go's
// `row[key]` is nil for both, so asFloat's old `case nil` silently defaulted
// a null score to 1.0 and imported a row Python refuses.
//
// A zero value that has to mean two things means neither (lens 15) — here
// nil meant "absent" and "null", and only the map's second return value
// separates them.
func pyFloatGet(row map[string]any, key string, def float64) (float64, error) {
	v, present := row[key]
	if !present {
		return def, nil
	}
	return asFloat(v, def)
}

// pyGet is `row.get(key, def)` — the same presence rule, for a value that
// is STORED rather than coerced. `original_tier` is the live instance:
// `row.get("tier", "")` writes "" for an absent key and the JSON null
// through for a present one, and the fix that removed a wrong `str()` from
// this site put `row["tier"]` in its place — which is nil for BOTH, so an
// absent tier started writing `null` into a shared store where CPython
// writes `""`.
//
// That inversion is the reason this is a named helper and not an inline
// two-liner. The idiom has now cost a divergence three times, each time
// spelled differently, and each fix was correct about the case that
// prompted it and wrong about the case it did not have a fixture for. The
// map's second return value is the only thing that separates absent from
// null, so it belongs in one place that every site reaches.
func pyGet(row map[string]any, key string, def any) any {
	if v, present := row[key]; present {
		return v
	}
	return def
}

// pyStrGet is `str(row.get(key, def))` — pyGet, then Python's str().
//
// The two rules compose into three outcomes, and the port had all three
// collapsed into one: an ABSENT key gives str(def), a null gives "None",
// and anything else gives its str(). `asString(row[key])` gave "" for the
// first two, which put "" into `task_type` and `outcome` on a row CPython
// stores as the literal string "None".
func pyStrGet(row map[string]any, key string, def any) string {
	return asString(pyGet(row, key, def))
}

func asFloat(v any, def float64) (float64, error) {
	switch t := v.(type) {
	case nil:
		// `float(None)`, reproduced sentence and all: the error text rides
		// the malformed_skipped result row, where an operator reads it.
		return 0, fmt.Errorf(pyFloatTypeErr, "NoneType")
	case float64:
		return t, nil
	case json.Number:
		f, ferr := t.Float64()
		if ferr != nil && math.IsInf(f, 0) {
			// A literal past float64's range. CPython's json.loads has
			// ALREADY turned `1e400` into inf by the time float() sees it,
			// and float(inf) succeeds — so the row imports there. Go's
			// Float64 reports the same ±Inf alongside ErrRange, and
			// treating that as a fault made the row malformed_skipped
			// here. The value agrees; only the error did not.
			//
			// REACHABILITY, measured rather than assumed: neither
			// exporter can produce this. Both normalize a float literal
			// through loads→dumps before it enters a pack, so `1e400`
			// arrives as the token `Infinity` — and json.Number("Infinity")
			// .Float64() returns +Inf with NO error, missing this branch
			// entirely. What reaches it is a HAND-BUILT pack, which is
			// exactly the input this whole file treats as adversarial.
			// Pinned by TestAsFloatMatchesPythonFloat rather than through
			// the pipeline, because the pipeline cannot express it.
			return f, nil
		}
		return f, ferr
	case string:
		// float(str), not TrimSpace + strconv (mission-r7 MEDIUM).
		if f, ok := pyval.ParseFloat(t); ok {
			return f, nil
		}
		// ValueError, not TypeError — a different sentence AND a different
		// shape: this one quotes the offending value with repr().
		return 0, fmt.Errorf("could not convert string to float: %s",
			pytext.Repr(t))
	case bool:
		if t {
			return 1, nil
		}
		return 0, nil
	default:
		return 0, fmt.Errorf(pyFloatTypeErr, pyTypeName(v))
	}
}

// pyFloatTypeErr is CPython's TypeError sentence for float(x), and
// pyTypeName is the type name it interpolates.
//
// These are content keys, not diagnostics. The text lands verbatim in a
// `malformed_skipped` result row that both runtimes write to a shared audit
// ledger, so `not a number: map[]` is not merely uglier than CPython's
// wording — it is a different stored value, and Go's %v spelling of a map
// leaks into a row an operator reads.
//
// The same commit that removed a `"score: "` prefix from this error path,
// with a comment explaining that the error text is a content key, left the
// error text itself invented at three of four branches. Naming the class
// was not the same as sweeping it (lens 3).
const pyFloatTypeErr = "float() argument must be a string or a real number, not '%s'"

func pyTypeName(v any) string {
	switch v.(type) {
	case nil:
		return "NoneType"
	case bool:
		return "bool"
	case string:
		return "str"
	case []any:
		return "list"
	case map[string]any:
		return "dict"
	case json.Number, float64, int:
		return "float"
	}
	// Every value here came out of a JSON decode, so the cases above are
	// exhaustive for real input. A Go type name is a wrong answer rather
	// than a missing one, but it is at least visibly foreign — better than
	// silently claiming a Python type this value does not have.
	return fmt.Sprintf("%T", v)
}

// pyStrOr is `str(v or "")` — str() of the value, but "" for anything
// FALSY.
//
// Python writes the provenance classifier's two arguments that way and
// writes the stored fields as bare `.get(k, "")`, and the difference is not
// decoration: `str(0)` is "0" and `str(0 or "")` is "". The classifier's
// input decides whether a lesson is quarantined, so a falsy non-string in
// either field made the two runtimes classify the same row differently.
func pyStrOr(v any) string {
	if !pyval.Truthy(v) {
		return ""
	}
	return asString(v)
}

func asString(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case nil:
		// `str(None)` is "None", not "". This arm used to return "", which
		// made asString a str() that is wrong about exactly one value —
		// and that value is what an explicit JSON null decodes to, so the
		// arm was wrong precisely where a foreign pack could reach it.
		//
		// Only pyStrGet and the two typed lesson fields reach nil here:
		// rowID's nil case is handled before the call, and pyStrOr guards
		// on Truthy. Callers that want ""-for-absent must say so with
		// pyGet's default, which is what Python's own `.get(k, "")` does.
		return "None"
	case json.Number:
		return t.String()
	case float64:
		return strconv.FormatFloat(t, 'g', -1, 64)
	case bool:
		if t {
			return "True"
		}
		return "False"
	default:
		return fmt.Sprintf("%v", t)
	}
}

// importLessons: MEDIUM tier regardless of origin tier, score capped 0.5,
// counters reset, provenance gate re-applied (conservative union — either
// side saying "prompt" quarantines).
func (im *importer) importLessons(content string) ([]pyval.Obj, error) {
	snap, err := im.store.LessonSnapshot()
	if err != nil {
		return nil, err
	}
	var results []pyval.Obj
	for _, sr := range scanRows(content) {
		if sr.err != nil {
			results = append(results, malformedRow("lesson_id", "", sr.err))
			continue
		}
		row := sr.row
		originalID, rawID, err := rowID(row, "lesson_id")
		if err != nil {
			results = append(results, malformedRow("lesson_id", rawID, err))
			continue
		}
		res := im.importOneLesson(row, originalID, snap)
		results = append(results, res)
	}
	return results, nil
}

func (im *importer) importOneLesson(row map[string]any, originalID string,
	snap *knowledge.DedupSnapshot) pyval.Obj {
	lessonText, _ := row["lesson"].(string)
	newID := fmt.Sprintf("imported-%s-%s", im.packName, originalID)
	if snap.IDs[newID] {
		return pyval.Obj{
			{Key: "lesson_id", Val: originalID},
			{Key: "outcome", Val: "already_imported"}}
	}
	if lessonText != "" && snap.Texts[lessonText] {
		// Identity collision skips the ROW, not its rationale — incoming
		// variants union into the local twin (transport must not be
		// order-dependent; 2026-08-11 fixpoint). Data crosses, trust does not.
		inVars := importVariants(row["merged_variants"])
		if len(inVars) > 0 && !im.dryRun {
			if err := im.store.UnionVariantsIntoLesson(lessonText, inVars); err != nil {
				return malformedRow("lesson_id", originalID, err)
			}
		}
		// pack.py:727-728 — lesson_id, outcome, variants_merged.
		return pyval.Obj{
			{Key: "lesson_id", Val: originalID},
			{Key: "outcome", Val: "skipped_identical"},
			{Key: "variants_merged", Val: len(inVars)}}
	}

	// The error text is `str(exc)` from Python's per-row except and it rides
	// the result row an operator reads, so it is a content key: no "score: "
	// prefix, however much more useful one would be. Which field failed is
	// recoverable — the two messages differ only when the two values do, and
	// a helpful prefix that the other runtime does not write is one more
	// string an operator cannot grep across both.
	originalScore, err := pyFloatGet(row, "score", 1.0)
	if err != nil {
		return malformedRow("lesson_id", originalID, err)
	}
	confidence, err := pyFloatGet(row, "confidence", 0.5)
	if err != nil {
		// Coerce at the border: a junk value costs the import (reported),
		// not the store (a non-numeric confidence is load-fatal in Python's
		// knowledge_web).
		return malformedRow("lesson_id", originalID, err)
	}

	scopeClean, scopeBad := knowledge.CoerceScope(row["scope"])
	if scopeBad {
		// log.warning("pack %s: lesson %s carries an off-vocabulary scope
		// %r — imported unstamped", ...). Three pieces, and the port had
		// invented two of them: a "warn: " prefix that appears nowhere else
		// in this port or in Python, and %v where Python writes %r. The
		// value is `row.get("scope")` with NO default, so an absent scope
		// reprs as None — which is why this reads pyGet(..., nil) rather
		// than the coerced string.
		fmt.Fprintf(os.Stderr, "pack %s: lesson %s carries an off-vocabulary "+
			"scope %s — imported unstamped\n", im.packTag, originalID,
			pyRepr(pyGet(row, "scope", nil)))
	}

	// Python's dict literal, in Python's INSERTION order — this block ships
	// verbatim into medium/lessons.jsonl, which the other runtime reads.
	imported := pyval.Obj{
		{Key: "imported_from", Val: im.label},
		{Key: "pack", Val: im.packTag},
		{Key: "original_id", Val: originalID},
		// row.get("tier", "") — RAW, unlike the task_type/outcome pair
		// further down, which Python does wrap in str(). The asymmetry is
		// in the source and it is load-bearing here because this block
		// ships verbatim: a numeric tier stores as 5 there and "5" here.
		{Key: "original_tier", Val: pyGet(row, "tier", "")},
		{Key: "original_trust", Val: originalScore},
		{Key: "imported_at", Val: im.now},
	}
	if pyval.Truthy(row["imported"]) { // the lessons lane's copy of the same idiom
		imported.Set("original_provenance", pyval.FromPlain(row["imported"]))
	}

	// An incoming stamp is a CLAIM in untrusted JSON, not provenance —
	// normalize to the known enum, discard anything else. Then always run
	// the local Tier-0 classifier too and take the conservative union.
	incoming := ""
	if s, ok := row["minted_from"].(string); ok {
		// `raw_stamp.strip().lower()`. Both halves matter and this one is
		// a GATE: the retrieval quarantine matches the exact string
		// "prompt", and the normalizer is what turns a foreign exporter's
		// spelling into it. "prompt\x1c" strips to "prompt" in Python and
		// stays outside the enum here, so the incoming claim is discarded
		// and the row imports unquarantined on the port alone.
		incoming = pytext.Lower(pytext.Strip(s))
	}
	if incoming != "prompt" && incoming != "outcome" {
		incoming = ""
	}
	localClass := ""
	if im.provGate {
		// `classify_lesson_provenance(str(row.get("lesson") or ""),
		// str(row.get("source_goal") or ""))` — str-of-or-empty on BOTH,
		// which is not how either value is stored two dozen lines down.
		// lessonText is the `.(string)` read, so a numeric lesson reaches
		// the classifier as "" here and as "5" in CPython.
		localClass = provenance.Classify(
			pyStrOr(row["lesson"]), pyStrOr(row["source_goal"]), "")
	}
	mintedFrom := incoming
	if incoming == "prompt" || localClass == "prompt" {
		mintedFrom = "prompt"
	} else if incoming == "" {
		mintedFrom = localClass
	}

	lessonType := ""
	if lt, ok := row["lesson_type"].(string); ok && knowledge.LessonTypes[lt] {
		lessonType = lt
	}
	// Truthiness-preserving coercion, shared with the tiered loader —
	// the shape-only assertion here was the r1 citedness fix's unfixed
	// WRITER sibling: a drifted truthy value imported as [] and was
	// PERSISTED that way, so every future read saw a genuinely-uncited
	// row forever (adversarial recall r2 2026-08-22, both lenses).
	evidence := knowledge.CoerceEvidenceSources(row["evidence_sources"])
	if evidence == nil {
		evidence = []any{}
	}
	// Truthiness again, NOT shape: the same writer-sibling class as the
	// evidence coercion above, one field down — a shape-only .(bool) read
	// "provisional": "true" as false and silently promoted the row past
	// the recall-time provisional gate (adversarial recall r3 2026-08-22,
	// the flagship pattern's next instance).
	provisional := knowledge.Truthy(row["provisional"])
	score := originalScore
	if score > 0.5 {
		score = 0.5
	}

	// NAMED DIVERGENCE, and it is the typed struct rather than a mistake.
	// Python stores `lesson` and `source_goal` RAW (`row.get(k, "")`, no
	// str(), unlike the task_type/outcome pair below), so a numeric lesson
	// lands in the store as a JSON number there. TieredLesson.Lesson and
	// .SourceGoal are Go strings, so the port stringifies — and for `lesson`
	// specifically the `.(string)` read above ERASES a non-string to "",
	// which is worse than stringifying it: an empty lesson row.
	//
	// Not fixed by widening the fields to `any`: that ripples through every
	// reader in the knowledge package to buy fidelity on a shape no writer
	// in either runtime produces. Written down instead, because an unwritten
	// divergence is one the next reader re-derives — and if a foreign pack
	// ever does ship one, this comment is where the fix starts.
	tl := knowledge.TieredLesson{
		LessonID: newID, Lesson: lessonText,
		// `row.get("source_goal", "")` with NO str() around it, so an
		// explicit null is stored as null. This field is typed `string`
		// here and cannot hold one — the raw-value-vs-typed-struct
		// residual PORT.md already names, narrowed: absent agrees ("" both
		// sides), null does not (CPython writes null, this writes "").
		SourceGoal: asString(pyGet(row, "source_goal", "")),
		Confidence: confidence,
		// These two ARE wrapped in str() on the Python side, so a null is
		// the literal "None" in the store — not "".
		TaskType: pyStrGet(row, "task_type", ""),
		Outcome:  pyStrGet(row, "outcome", ""),
		Tier:     "medium",
		Score:    score,
		// Transaction time: decay reads last_reinforced, so this is what
		// stops a 3-month-old import from arriving half-decayed — it gets a
		// fair local hearing starting now.
		LastReinforced:    im.now[:10],
		SessionsValidated: 0, TimesApplied: 0, TimesReinforced: 0,
		RecordedAt:      im.now,
		EvidenceSources: evidence,
		LessonType:      lessonType,
		Imported:        imported,
		Provisional:     provisional,
		MintedFrom:      mintedFrom,
		Scope:           scopeClean,
		Contested:       map[string]any{},
		MergedVariants:  importVariants(row["merged_variants"]),
		DeltaEvidence:   map[string]any{},
		Grounding:       []map[string]any{},
		Canon:           map[string]any{},
	}
	if tl.MergedVariants == nil {
		tl.MergedVariants = []string{}
	}
	if !im.dryRun {
		if err := im.store.AppendMediumLesson(tl); err != nil {
			return malformedRow("lesson_id", originalID, err)
		}
	}
	snap.IDs[newID] = true
	// Duplicate rows INSIDE one artifact must not both import — the
	// identity snapshot was taken once, before any appends.
	snap.Texts[lessonText] = true
	outcome := "imported_medium"
	if mintedFrom == "prompt" {
		outcome = "imported_medium_quarantined"
	}
	// pack.py:850 — lesson_id, new_id, outcome.
	return pyval.Obj{
		{Key: "lesson_id", Val: originalID},
		{Key: "new_id", Val: newID},
		{Key: "outcome", Val: outcome}}
}

func (im *importer) quarantineDir() string {
	return filepath.Join(im.ws, "imports", im.label)
}

// writeQuarantine atomically writes quarantined content if it differs;
// returns true when the requested content was already present.
func (im *importer) writeQuarantine(path, content string) (bool, error) {
	// The lock file lives beside the target, so the parent must exist
	// before the lock can be taken (first live cross-runtime import hit
	// exactly this).
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return false, err
	}
	already := false
	err := record.Locked(path, func() error {
		// read_text, and only FileNotFoundError is caught. Everything else
		// PROPAGATES in Python — a quarantine file that is unreadable or not
		// UTF-8 aborts the import rather than being overwritten. `os.ReadFile
		// ... err == nil` swallowed both: a byte-tainted file compared
		// unequal and was replaced, so the port destroyed a file the other
		// runtime refuses to touch.
		//
		// And the comparison itself needs the newline translation: a
		// quarantine file written with CRLF is `already_quarantined` to
		// Python and a fresh `quarantined` write here.
		switch text, rerr := pyval.ReadText(path); {
		case rerr == nil && text == content:
			already = true
			return nil
		case rerr != nil && !os.IsNotExist(rerr):
			return rerr
		}
		// record.AtomicWrite, not a second temp-file dance. CPython's
		// _write_quarantine calls file_lock.atomic_write, which fchmods
		// the temp before publishing it; the hand-rolled version here
		// renamed an os.CreateTemp file into place, and os.CreateTemp
		// creates 0600. Every quarantined artifact landed 0600 where
		// CPython leaves 0664 -- and quarantine exists precisely so a
		// human, or the other runtime, can come and look at it.
		return record.AtomicWrite(path, []byte(content))
	})
	return already, err
}

// quarantineSingle ports _quarantine_single (pack.py:1010-1019), whose
// return is {path, outcome} — the two keys its callers then spread a
// `class` in FRONT of.
func (im *importer) quarantineSingle(relpath, content string) (pyval.Obj, error) {
	path := filepath.Join(im.quarantineDir(), filepath.FromSlash(relpath))
	if im.dryRun {
		text, rerr := pyval.ReadText(path)
		if rerr != nil && !os.IsNotExist(rerr) {
			return nil, rerr // `path.exists() and path.read_text(...)`
		}
		return quarantineRow(relpath, rerr == nil && text == content), nil
	}
	already, err := im.writeQuarantine(path, content)
	if err != nil {
		return nil, err
	}
	return quarantineRow(relpath, already), nil
}

func quarantineRow(relpath string, already bool) pyval.Obj {
	return pyval.Obj{
		{Key: "path", Val: relpath},
		{Key: "outcome", Val: quarantineOutcome(already)},
	}
}

func quarantineOutcome(already bool) string {
	if already {
		return "already_quarantined"
	}
	return "quarantined"
}

// importAuthoredMD: Class A (.md) never lands live — always quarantine.
// Same-name/different-content vs a local file: local wins, noted in
// CONFLICTS.md.
func (im *importer) importAuthoredMD(kind, relpath, content string) (pyval.Obj, error) {
	name := filepath.Base(filepath.FromSlash(relpath))
	livePath := filepath.Join(im.ws, kind, name)
	quarantinePath := filepath.Join(im.quarantineDir(), kind, name)

	// `if live_path.exists(): live_path.read_text(...) == content`. The
	// translation decides the OUTCOME, not just the bytes: a local skill
	// saved with CRLF is `skipped_identical` to Python and
	// `conflict_quarantined` here — the port writes a quarantine file and a
	// CONFLICTS.md row for a file the other runtime calls unchanged.
	if text, err := pyval.ReadText(livePath); err == nil || !os.IsNotExist(err) {
		if err != nil {
			return nil, err
		}
		if text == content {
			return authoredRow(name, "skipped_identical"), nil
		}
		if !im.dryRun {
			if _, err := im.writeQuarantine(quarantinePath, content); err != nil {
				return nil, err
			}
			if err := im.appendConflictsNote(kind, name); err != nil {
				return nil, err
			}
		}
		return authoredRow(name, "conflict_quarantined"), nil
	}

	if im.dryRun {
		text, rerr := pyval.ReadText(quarantinePath)
		if rerr != nil && !os.IsNotExist(rerr) {
			return nil, rerr
		}
		return authoredRow(name, quarantinedMDOutcome(rerr == nil && text == content)), nil
	}
	already, err := im.writeQuarantine(quarantinePath, content)
	if err != nil {
		return nil, err
	}
	return authoredRow(name, quarantinedMDOutcome(already)), nil
}

// authoredRow ports _quarantine_authored's return (pack.py:1004-1007) and
// the two early exits above it: {name, outcome}, in that order.
func authoredRow(name, outcome string) pyval.Obj {
	return pyval.Obj{
		{Key: "name", Val: name},
		{Key: "outcome", Val: outcome},
	}
}

func quarantinedMDOutcome(already bool) string {
	if already {
		return "already_quarantined"
	}
	return "quarantined"
}

func (im *importer) appendConflictsNote(kind, name string) error {
	path := filepath.Join(im.quarantineDir(), "CONFLICTS.md")
	line := fmt.Sprintf("- `%s/%s` — local version differs; import kept in "+
		"quarantine, local wins (%s)", kind, name, im.now)
	header := fmt.Sprintf("# Conflicts — %s\n\n"+
		"Same-name, different-content collisions between this pack's skills/"+
		"personas and local ones. Local always wins; these stay in quarantine "+
		"— adopt is editorial, not automatic.\n\n", im.label)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return record.Locked(path, func() error {
		old := ""
		if raw, err := os.ReadFile(path); err == nil {
			old = string(raw)
		}
		// `line in old.splitlines()`. Nothing can reach the difference —
		// this function is the only writer of these lines and their format
		// is fixed — but it is the same class as scanRows above, and a
		// spelling left un-fixed because it is currently unreachable is one
		// nobody re-examines when it stops being unreachable.
		for _, existing := range pytext.SplitLines(old) {
			if existing == line {
				return nil
			}
		}
		content := old
		if content == "" {
			content = header
		}
		content += line + "\n"
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return err
		}
		return os.WriteFile(path, []byte(content), 0o644)
	})
}

// pyRepr is Python's repr() for the JSON value types, for the operator
// sentences that interpolate %r. pytext.Repr covers the string case, which
// is the one that quote-switches; the others are short and exact.
func pyRepr(v any) string {
	switch t := v.(type) {
	case nil:
		return "None"
	case string:
		return pytext.Repr(t)
	case bool:
		if t {
			return "True"
		}
		return "False"
	case json.Number:
		return t.String()
	}
	// A list or dict reprs with Python's spacing, which pyval already
	// knows how to write. Not reachable from the one live caller (an
	// off-vocabulary scope that is a container fails CoerceScope's string
	// check first), so this is a floor rather than a claim.
	return fmt.Sprintf("%v", v)
}
