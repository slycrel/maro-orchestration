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
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/knowledge"
	"github.com/slycrel/maro-orchestration/go/internal/provenance"
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
type ImportReport struct {
	Pack                     string           `json:"pack"`
	PackTag                  string           `json:"pack_tag"`
	Label                    string           `json:"label"`
	ImportedAt               string           `json:"imported_at"`
	DryRun                   bool             `json:"dry_run"`
	RulesDemotedToHypotheses []map[string]any `json:"rules_demoted_to_hypotheses"`
	HypothesesImported       []map[string]any `json:"hypotheses_imported"`
	LessonsImported          []map[string]any `json:"lessons_imported"`
	SkillRecordsImported     []map[string]any `json:"skill_records_imported"`
	SkillsMD                 []map[string]any `json:"skills_md"`
	PersonasMD               []map[string]any `json:"personas_md"`
	Quarantined              []map[string]any `json:"quarantined"`
	QuarantinedUnknown       []map[string]any `json:"quarantined_unknown"`
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

func artifactRelpath(a map[string]any) (string, error) {
	p, _ := a["path"].(string)
	rel := strings.TrimPrefix(p, "artifacts/")
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
	if raw, present := manifest["pack_format"]; present {
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

	review, _ := manifest["review"].(map[string]any)
	humanReviewed, _ := review["human_reviewed"].(bool)
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
		p, _ := a["path"].(string)
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
		expected, _ := review["review_manifest_sha256"].(string)
		if expected == "" || sha256Text(archivedReview) != expected {
			return nil, fmt.Errorf(
				"import refused: REVIEW.md in the archive does not match the sealed " +
					"hash — possible post-seal tampering")
		}
		expectedPayload, _ := review["review_payload_sha256"].(string)
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
		p, _ := a["path"].(string)
		declared, _ := a["sha256"].(string)
		if declared == "" || sha256Text(string(artifactBytes[p])) != declared {
			return nil, fmt.Errorf(
				"import refused: artifact %q does not match its manifest sha256 — "+
					"possible post-seal tampering", p)
		}
	}

	packName, _ := manifest["name"].(string)
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
		RulesDemotedToHypotheses: []map[string]any{},
		HypothesesImported:       []map[string]any{},
		LessonsImported:          []map[string]any{},
		SkillRecordsImported:     []map[string]any{},
		SkillsMD:                 []map[string]any{},
		PersonasMD:               []map[string]any{},
		Quarantined:              []map[string]any{},
		QuarantinedUnknown:       []map[string]any{},
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
			config.Get[any](cfg, "knowledge.provenance_gate_enabled", true)),
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
			cls, _ := artifact["class"].(string)
			rel, err := artifactRelpath(artifact)
			if err != nil {
				return err
			}
			p, _ := artifact["path"].(string)
			content := string(artifactBytes[p])
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
				res["outcome"] = "quarantined_no_native_skill_store_v1"
				res["class"] = cls
				report.SkillRecordsImported = append(report.SkillRecordsImported, res)
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
				res["class"] = cls
				report.Quarantined = append(report.Quarantined, res)
			default:
				res, err := imp.quarantineSingle("unknown/"+rel, content)
				if err != nil {
					return err
				}
				res["class"] = cls
				report.QuarantinedUnknown = append(report.QuarantinedUnknown, res)
			}
		}
		if !opts.DryRun {
			raw, err := json.Marshal(struct {
				*ImportReport
				Action string `json:"action"`
			}{report, "pack_import"})
			if err != nil {
				return err
			}
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
	for _, line := range strings.Split(content, "\n") {
		line = strings.TrimSpace(line)
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

func (im *importer) provenanceStamp(originalID, originalClass string, row map[string]any) map[string]any {
	imported := map[string]any{
		"imported_from": im.label, "pack": im.packTag,
		"original_id": originalID, "original_class": originalClass,
		"original_confirmations":  row["confirmations"],
		"original_contradictions": row["contradictions"],
		"imported_at":             im.now,
	}
	if prov, ok := row["imported"].(map[string]any); ok && len(prov) > 0 {
		imported["original_provenance"] = prov
	}
	return imported
}

// importRulesAsHypotheses: standing rules demote to Hypothesis on arrival.
func (im *importer) importRulesAsHypotheses(content string) ([]map[string]any, error) {
	snap, err := im.store.HypothesisSnapshot()
	if err != nil {
		return nil, err
	}
	var results []map[string]any
	for _, sr := range scanRows(content) {
		if sr.err != nil {
			results = append(results, map[string]any{
				"rule_id": "", "outcome": "malformed_skipped", "error": sr.err.Error()})
			continue
		}
		row := sr.row
		originalID, rawID, err := rowID(row, "rule_id")
		if err != nil {
			results = append(results, map[string]any{
				"rule_id": rawID, "outcome": "malformed_skipped", "error": err.Error()})
			continue
		}
		ruleText, _ := row["rule"].(string)
		hypID := fmt.Sprintf("imported-%s-%s", im.packName, originalID)
		if snap.IDs[hypID] {
			results = append(results, map[string]any{"rule_id": originalID, "outcome": "already_imported"})
			continue
		}
		if ruleText != "" && snap.Texts[ruleText] {
			results = append(results, map[string]any{"rule_id": originalID, "outcome": "skipped_identical"})
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
				results = append(results, map[string]any{
					"rule_id": originalID, "outcome": "malformed_skipped", "error": err.Error()})
				continue
			}
		}
		snap.IDs[hypID] = true
		results = append(results, map[string]any{
			"rule_id": originalID, "hyp_id": hypID, "outcome": "demoted_to_hypothesis"})
	}
	return results, nil
}

// importHypotheses: already-hypothesis rows still reset trust on arrival.
func (im *importer) importHypotheses(content string) ([]map[string]any, error) {
	snap, err := im.store.HypothesisSnapshot()
	if err != nil {
		return nil, err
	}
	var results []map[string]any
	for _, sr := range scanRows(content) {
		if sr.err != nil {
			results = append(results, map[string]any{
				"hyp_id": "", "outcome": "malformed_skipped", "error": sr.err.Error()})
			continue
		}
		row := sr.row
		originalID, rawID, err := rowID(row, "hyp_id")
		if err != nil {
			results = append(results, map[string]any{
				"hyp_id": rawID, "outcome": "malformed_skipped", "error": err.Error()})
			continue
		}
		lessonText, _ := row["lesson"].(string)
		hypID := fmt.Sprintf("imported-%s-%s", im.packName, originalID)
		if snap.IDs[hypID] {
			results = append(results, map[string]any{"hyp_id": originalID, "outcome": "already_imported"})
			continue
		}
		if lessonText != "" && snap.Texts[lessonText] {
			results = append(results, map[string]any{"hyp_id": originalID, "outcome": "skipped_identical"})
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
				results = append(results, map[string]any{
					"hyp_id": originalID, "outcome": "malformed_skipped", "error": err.Error()})
				continue
			}
		}
		snap.IDs[hypID] = true
		results = append(results, map[string]any{
			"hyp_id": originalID, "new_hyp_id": hypID, "outcome": "imported"})
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
		if !ok || strings.TrimSpace(s) == "" {
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
func asFloat(v any, def float64) (float64, error) {
	switch t := v.(type) {
	case nil:
		return def, nil
	case float64:
		return t, nil
	case json.Number:
		return t.Float64()
	case string:
		// float(str), not TrimSpace + strconv (mission-r7 MEDIUM).
		if f, ok := pyval.ParseFloat(t); ok {
			return f, nil
		}
		return 0, fmt.Errorf("not a number: %v", v)
	case bool:
		if t {
			return 1, nil
		}
		return 0, nil
	default:
		return 0, fmt.Errorf("not a number: %v", v)
	}
}

func asString(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case nil:
		return ""
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
func (im *importer) importLessons(content string) ([]map[string]any, error) {
	snap, err := im.store.LessonSnapshot()
	if err != nil {
		return nil, err
	}
	var results []map[string]any
	for _, sr := range scanRows(content) {
		if sr.err != nil {
			results = append(results, map[string]any{
				"lesson_id": "", "outcome": "malformed_skipped", "error": sr.err.Error()})
			continue
		}
		row := sr.row
		originalID, rawID, err := rowID(row, "lesson_id")
		if err != nil {
			results = append(results, map[string]any{
				"lesson_id": rawID, "outcome": "malformed_skipped", "error": err.Error()})
			continue
		}
		res := im.importOneLesson(row, originalID, snap)
		results = append(results, res)
	}
	return results, nil
}

func (im *importer) importOneLesson(row map[string]any, originalID string,
	snap *knowledge.DedupSnapshot) map[string]any {
	lessonText, _ := row["lesson"].(string)
	newID := fmt.Sprintf("imported-%s-%s", im.packName, originalID)
	if snap.IDs[newID] {
		return map[string]any{"lesson_id": originalID, "outcome": "already_imported"}
	}
	if lessonText != "" && snap.Texts[lessonText] {
		// Identity collision skips the ROW, not its rationale — incoming
		// variants union into the local twin (transport must not be
		// order-dependent; 2026-08-11 fixpoint). Data crosses, trust does not.
		inVars := importVariants(row["merged_variants"])
		if len(inVars) > 0 && !im.dryRun {
			if err := im.store.UnionVariantsIntoLesson(lessonText, inVars); err != nil {
				return map[string]any{"lesson_id": originalID,
					"outcome": "malformed_skipped", "error": err.Error()}
			}
		}
		return map[string]any{"lesson_id": originalID,
			"outcome": "skipped_identical", "variants_merged": len(inVars)}
	}

	originalScore, err := asFloat(row["score"], 1.0)
	if err != nil {
		return map[string]any{"lesson_id": originalID,
			"outcome": "malformed_skipped", "error": "score: " + err.Error()}
	}
	confidence, err := asFloat(row["confidence"], 0.5)
	if err != nil {
		// Coerce at the border: a junk value costs the import (reported),
		// not the store (a non-numeric confidence is load-fatal in Python's
		// knowledge_web).
		return map[string]any{"lesson_id": originalID,
			"outcome": "malformed_skipped", "error": "confidence: " + err.Error()}
	}

	scopeClean, scopeBad := knowledge.CoerceScope(row["scope"])
	if scopeBad {
		fmt.Fprintf(os.Stderr, "warn: pack %s: lesson %s carries an off-vocabulary "+
			"scope %v — imported unstamped\n", im.packTag, originalID, row["scope"])
	}

	imported := map[string]any{
		"imported_from": im.label, "pack": im.packTag,
		"original_id": originalID, "original_tier": asString(row["tier"]),
		"original_trust": originalScore, "imported_at": im.now,
	}
	if prov, ok := row["imported"].(map[string]any); ok && len(prov) > 0 {
		imported["original_provenance"] = prov
	}

	// An incoming stamp is a CLAIM in untrusted JSON, not provenance —
	// normalize to the known enum, discard anything else. Then always run
	// the local Tier-0 classifier too and take the conservative union.
	incoming := ""
	if s, ok := row["minted_from"].(string); ok {
		incoming = strings.ToLower(strings.TrimSpace(s))
	}
	if incoming != "prompt" && incoming != "outcome" {
		incoming = ""
	}
	localClass := ""
	if im.provGate {
		localClass = provenance.Classify(lessonText, asString(row["source_goal"]), "")
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

	tl := knowledge.TieredLesson{
		LessonID: newID, Lesson: lessonText,
		SourceGoal: asString(row["source_goal"]),
		Confidence: confidence,
		TaskType:   asString(row["task_type"]),
		Outcome:    asString(row["outcome"]),
		Tier:       "medium",
		Score:      score,
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
			return map[string]any{"lesson_id": originalID,
				"outcome": "malformed_skipped", "error": err.Error()}
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
	return map[string]any{"lesson_id": originalID, "new_id": newID, "outcome": outcome}
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
		if raw, err := os.ReadFile(path); err == nil && string(raw) == content {
			already = true
			return nil
		}
		tmp, err := os.CreateTemp(filepath.Dir(path), ".quarantine-*")
		if err != nil {
			return err
		}
		defer os.Remove(tmp.Name())
		if _, err := tmp.WriteString(content); err != nil {
			tmp.Close()
			return err
		}
		if err := tmp.Close(); err != nil {
			return err
		}
		return os.Rename(tmp.Name(), path)
	})
	return already, err
}

func (im *importer) quarantineSingle(relpath, content string) (map[string]any, error) {
	path := filepath.Join(im.quarantineDir(), filepath.FromSlash(relpath))
	if im.dryRun {
		already := false
		if raw, err := os.ReadFile(path); err == nil && string(raw) == content {
			already = true
		}
		return map[string]any{"path": relpath,
			"outcome": quarantineOutcome(already)}, nil
	}
	already, err := im.writeQuarantine(path, content)
	if err != nil {
		return nil, err
	}
	return map[string]any{"path": relpath, "outcome": quarantineOutcome(already)}, nil
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
func (im *importer) importAuthoredMD(kind, relpath, content string) (map[string]any, error) {
	name := filepath.Base(filepath.FromSlash(relpath))
	livePath := filepath.Join(im.ws, kind, name)
	quarantinePath := filepath.Join(im.quarantineDir(), kind, name)

	if raw, err := os.ReadFile(livePath); err == nil {
		if string(raw) == content {
			return map[string]any{"name": name, "outcome": "skipped_identical"}, nil
		}
		if !im.dryRun {
			if _, err := im.writeQuarantine(quarantinePath, content); err != nil {
				return nil, err
			}
			if err := im.appendConflictsNote(kind, name); err != nil {
				return nil, err
			}
		}
		return map[string]any{"name": name, "outcome": "conflict_quarantined"}, nil
	}

	if im.dryRun {
		already := false
		if raw, err := os.ReadFile(quarantinePath); err == nil && string(raw) == content {
			already = true
		}
		return map[string]any{"name": name,
			"outcome": quarantinedMDOutcome(already)}, nil
	}
	already, err := im.writeQuarantine(quarantinePath, content)
	if err != nil {
		return nil, err
	}
	return map[string]any{"name": name, "outcome": quarantinedMDOutcome(already)}, nil
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
		for _, existing := range strings.Split(old, "\n") {
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
