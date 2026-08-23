// Package evolver ports the meta-evolution slice of src/evolver.py +
// src/evolver_store.py (§19 self-leveling): review recent outcomes,
// propose improvements, persist them to memory/suggestions.jsonl, and
// auto-apply the low-risk categories with an audit trail.
//
// SLICE BOUNDARY (Go slice 1) — ported:
//   - Suggestion/EvolverReport with the FULL Python on-disk field set
//     (rows written by either runtime rehydrate in the other).
//   - suggestions.jsonl store: load, content-key dedup save, pending
//     list, dismiss, applied check, keyed-merge apply stamp.
//   - evolver_cadence.json locked tick (byte-compatible counter file).
//   - LLM outcome analysis (system prompt + guidance-form rules
//     verbatim) and the outcomes summary it reads.
//   - Apply engine for: observation (no-op), prompt_tweak (medium
//     tiered lesson, minted_by=evolver, dedup-by-text),
//     new_guardrail (dynamic-constraints.jsonl append behind the same
//     manual/env/config gate, default HELD), with the injection-guard
//     scan FAIL-CLOSED in front and a change_log.jsonl audit row
//     before every mutation.
//   - Revert via change_log: lesson_add is bookkeeping-only (Python
//     parity: lessons are append-only, decay handles cleanup),
//     guardrail_append removes the constraint row (behavioral).
//
// NAMED AS NOT PORTED (do not mistake absence for coverage):
// skill_pattern apply/revert + test gate (no Go skills store) — held
// for review; sub_mission enqueue (no Go goal queue) — held; the
// 0.6-0.79 advisor gate (medium-confidence rows stay pending); the
// statistical scanners (calibration/costs/drift/canon/suggestion-
// calibration), harness-friction, persona-gap, skill-candidate,
// island-model and graduation passes; verify_applied_suggestions'
// V2 cadence-verdict lifecycle; _verify_post_apply's test-suite run
// (Go auto-applies only data rows, and faking a pytest verdict would
// be dishonest — the revert path is the safety net this slice keeps);
// playbook.md append; Telegram notify; step traces in the outcomes
// summary (no Go step-trace store).
//
// Cross-runtime note: this store is SHARED with the Python runtime
// (same workspace, same .lock flock protocol). A Go-held row is
// applyable from the Python CLI and vice versa.
package evolver

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/guard"
	"github.com/slycrel/maro-orchestration/go/internal/knowledge"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Suggestion mirrors evolver_store.Suggestion.to_dict() — field order
// matches the Python dict so freshly-written rows diff cleanly against
// Python-written ones.
type Suggestion struct {
	SuggestionID     string  `json:"suggestion_id"`
	Category         string  `json:"category"` // prompt_tweak | new_guardrail | skill_pattern | observation | sub_mission | ...
	Target           string  `json:"target"`
	Suggestion       string  `json:"suggestion"`
	FailurePattern   string  `json:"failure_pattern"`
	Confidence       float64 `json:"confidence"`
	OutcomesAnalyzed int     `json:"outcomes_analyzed"`
	GeneratedAt      string  `json:"generated_at"`
	Applied          bool    `json:"applied"`
	AppliedAt        string  `json:"applied_at"`
	AppliedManually  bool    `json:"applied_manually"`
	// ExpectedSignal declares which observable this change should move
	// (VERIFY_LEARN_ARC V1 — capture only; the verdict lifecycle that
	// reads it is not ported).
	ExpectedSignal   []map[string]any `json:"expected_signal"`
	VerifiedAt       string           `json:"verified_at"`
	VerifyVerdict    string           `json:"verify_verdict"`
	VerifyExtensions int              `json:"verify_extensions"`
	// Pattern is new_guardrail only: the REGEX matched against step
	// text — distinct from Suggestion (prose). Writing prose into the
	// pattern slot produced guardrails that could never fire
	// (2026-08-04).
	Pattern     string `json:"pattern"`
	Status      string `json:"status"` // "" | held_for_review | pending_human_review | action_failed | dismissed | reverted | ...
	BlockReason string `json:"block_reason"`
	DismissedAt string `json:"dismissed_at"`
	PlaybookKey string `json:"playbook_key"`
}

// EvolverReport mirrors the Python dataclass.
type EvolverReport struct {
	RunID            string       `json:"run_id"`
	OutcomesReviewed int          `json:"outcomes_reviewed"`
	Suggestions      []Suggestion `json:"suggestions"`
	FailurePatterns  []string     `json:"failure_patterns"`
	ElapsedMS        int64        `json:"elapsed_ms"`
	Skipped          bool         `json:"skipped"`
	SkipReason       string       `json:"skip_reason"`
	// AutoApplied is Go-side reporting (Python prints it; the report
	// object doesn't carry it — additive, omitted when zero).
	AutoApplied int `json:"auto_applied,omitempty"`
}

// Summary renders the operator-facing block (Python summary()).
func (r EvolverReport) Summary() string {
	if r.Skipped {
		return fmt.Sprintf("evolver run_id=%s skipped: %s", r.RunID, r.SkipReason)
	}
	lines := []string{
		"evolver run_id=" + r.RunID,
		fmt.Sprintf("outcomes_reviewed=%d", r.OutcomesReviewed),
		fmt.Sprintf("suggestions=%d", len(r.Suggestions)),
		fmt.Sprintf("failure_patterns=%d", len(r.FailurePatterns)),
		fmt.Sprintf("elapsed_ms=%d", r.ElapsedMS),
	}
	for _, s := range r.Suggestions {
		lines = append(lines, fmt.Sprintf("  [%s] %s: %s", s.Category, s.Target, clipRunes(s.Suggestion, 80)))
	}
	return strings.Join(lines, "\n")
}

func clipRunes(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		r = r[:n]
	}
	return string(r)
}

func suggestionsPath(ws string) string {
	return filepath.Join(ws, "memory", "suggestions.jsonl")
}

func dynamicConstraintsPath(ws string) string {
	return filepath.Join(ws, "memory", "dynamic-constraints.jsonl")
}

func cadencePath(ws string) string {
	return filepath.Join(ws, "memory", "evolver_cadence.json")
}

func changeLogPath(ws string) string {
	return filepath.Join(ws, "memory", "change_log.jsonl")
}

func nowISO() string { return time.Now().UTC().Format(time.RFC3339Nano) }

// CadenceTick counts one run finalization toward the evolver cadence;
// returns true when the counter reaches cadence (and resets). Single
// locked RMW so concurrent finalizations can't both trigger. Callers
// must not count dry runs (Python contract). Unlike the inspector's
// tick, corrupt fields reset to 0 with NO negative clamp — fork-point
// parity: only the inspector's counter grew the clamp in review.
func CadenceTick(workspaceDir string, cadence int) (bool, error) {
	fired := false
	err := record.LockedRMW(cadencePath(workspaceDir), func(old string) string {
		count := 0
		var state map[string]any
		if json.Unmarshal([]byte(old), &state) == nil && state != nil {
			if f, ok := state["runs_since_evolve"].(float64); ok {
				count = int(f)
			}
		}
		count++
		if cadence > 0 && count >= cadence {
			fired = true
			count = 0
		}
		out, _ := json.Marshal(map[string]any{
			"runs_since_evolve": count,
			"updated_at":        nowISO(),
		})
		return string(out)
	})
	return fired, err
}

// readRows tolerantly reads all parseable rows from a jsonl file
// (missing file = empty store; a torn line costs one row).
func readRows(path string) []map[string]any {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var rows []map[string]any
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var m map[string]any
		if json.Unmarshal([]byte(line), &m) == nil {
			rows = append(rows, m)
		}
	}
	return rows
}

func rowToSuggestion(m map[string]any) Suggestion {
	raw, _ := json.Marshal(m)
	var s Suggestion
	_ = json.Unmarshal(raw, &s)
	// applied_manually guards the never-auto-revert invariant, and Python
	// reads it with bool() truthiness — a present-but-malformed value
	// ("true", 1) is truthy there and PROTECTS the row. Go's typed decode
	// zeroed it to false, routing a human-applied row into the auto-revert
	// branch (r1 security review) — the unsafe direction. Coerce like
	// Python; same for applied (the same failure shape, lower stakes).
	if v, present := m["applied_manually"]; present {
		s.AppliedManually = pyTruthy(v)
	}
	if v, present := m["applied"]; present {
		s.Applied = pyTruthy(v)
	}
	return s
}

// pyTruthy mirrors Python bool(): non-empty string, non-zero number, true.
func pyTruthy(v any) bool {
	switch t := v.(type) {
	case bool:
		return t
	case string:
		return t != ""
	case float64:
		return t != 0
	case nil:
		return false
	case []any:
		return len(t) > 0
	case map[string]any:
		return len(t) > 0
	}
	return true
}

// LoadSuggestions returns up to limit suggestions, newest first.
func LoadSuggestions(workspaceDir string, limit int) []Suggestion {
	rows := readRows(suggestionsPath(workspaceDir))
	var out []Suggestion
	for i := len(rows) - 1; i >= 0; i-- {
		out = append(out, rowToSuggestion(rows[i]))
		if limit > 0 && len(out) >= limit {
			break
		}
	}
	return out
}

// GetSuggestion returns the current on-disk row for one id, or nil —
// a single-row uncapped lookup that never drops the row behind a
// newest-N window (Python get_suggestion).
func GetSuggestion(workspaceDir, suggestionID string) *Suggestion {
	for _, m := range readRows(suggestionsPath(workspaceDir)) {
		if m["suggestion_id"] == suggestionID {
			s := rowToSuggestion(m)
			return &s
		}
	}
	return nil
}

// contentKey is the identity of a suggestion's FINDING, independent of
// its id: scans re-derive from their inputs every cycle and some mint a
// fresh uuid per derivation, so id equality can't detect "same finding
// again" — content equality can (Python _content_key; the 81-duplicate
// calibration-row bug).
func contentKey(category, target, suggestion string) string {
	return category + "\x00" + target + "\x00" + strings.TrimSpace(suggestion)
}

// SaveSuggestions appends rows whose finding is not already on disk.
// Dismissed and applied rows count as "already have it" — re-deriving
// identical content from an unmoved input must not resurrect a
// suggestion someone already reviewed.
func SaveSuggestions(workspaceDir string, suggestions []Suggestion) error {
	p := suggestionsPath(workspaceDir)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	// The dedup read and the appends happen under ONE lock: a `seen` set
	// built outside it reopens the 81-duplicate bug whenever two cadences
	// derive the same finding concurrently (r1 QA review — repro'd on the
	// first attempt). Whole-file RMW is DELIBERATE here, not an oversight
	// of the 8MB read bound: content dedup is full-history by contract
	// (Python parity), and a bounded window would silently resurrect
	// reviewed suggestions — the surface-wide rule (r3 review) is
	// whole-file where the SEMANTICS are whole-file (keyed merges,
	// full-history dedup), bounded tail where the semantics are tail-N.
	var marshalErr error
	err := record.LockedRMW(p, func(old string) string {
		seen := map[string]bool{}
		for _, line := range strings.Split(old, "\n") {
			s := strings.TrimSpace(line)
			if s == "" {
				continue
			}
			var m map[string]any
			if json.Unmarshal([]byte(s), &m) != nil {
				continue
			}
			cat, _ := m["category"].(string)
			tgt, _ := m["target"].(string)
			sug, _ := m["suggestion"].(string)
			seen[contentKey(cat, tgt, sug)] = true
		}
		out := old
		if out != "" && !strings.HasSuffix(out, "\n") {
			out += "\n" // frame a torn tail before appending
		}
		for _, s := range suggestions {
			key := contentKey(s.Category, s.Target, s.Suggestion)
			if seen[key] {
				continue
			}
			seen[key] = true
			if s.ExpectedSignal == nil {
				s.ExpectedSignal = []map[string]any{}
			}
			raw, err := json.Marshal(s)
			if err != nil {
				marshalErr = err
				continue
			}
			out += string(raw) + "\n"
		}
		return out
	})
	if err != nil {
		return err
	}
	return marshalErr
}

// ListPending returns suggestions awaiting a decision, newest first
// (not applied, not dismissed).
func ListPending(workspaceDir string, limit int) []Suggestion {
	all := LoadSuggestions(workspaceDir, 1000)
	var out []Suggestion
	for _, s := range all {
		if s.Applied || s.Status == "dismissed" {
			continue
		}
		out = append(out, s)
		if limit > 0 && len(out) >= limit {
			break
		}
	}
	return out
}

// IsApplied reads the durable post-gate state for one suggestion.
func IsApplied(workspaceDir, suggestionID string) bool {
	for _, m := range readRows(suggestionsPath(workspaceDir)) {
		if m["suggestion_id"] == suggestionID {
			// pyTruthy, NOT a typed assert — and a NAMED DIVERGENCE, not
			// parity: fork-point Python's suggestion_is_applied and the
			// re-apply guard are strict `is True` (only its dataclass
			// paths use bool()). The r2 split read (candidate path truthy,
			// this guard strict) turned a degraded row with a malformed
			// applied:"true" into a silent forever-candidate; unifying on
			// truthy is the SAFE direction — Python's strict guard lets
			// the same malformed row REPLAY its mutation on re-apply
			// (backport candidate #13).
			return pyTruthy(m["applied"])
		}
	}
	return false
}

// Dismiss marks a suggestion reviewed-and-declined — the other exit
// from pending. Nothing is deleted; the row keeps its text and gains a
// dismissal stamp. Unparseable lines are re-emitted verbatim, never
// re-dumped (the loads_clean posture: a tainted line must not be
// laundered into clean escapes).
func Dismiss(workspaceDir, suggestionID, reason string) (bool, error) {
	p := suggestionsPath(workspaceDir)
	if _, err := os.Stat(p); err != nil {
		return false, nil
	}
	found := false
	err := record.LockedRMW(p, func(old string) string {
		var out []string
		for _, line := range strings.Split(old, "\n") {
			s := strings.TrimSpace(line)
			if s == "" {
				continue
			}
			var row map[string]any
			if json.Unmarshal([]byte(s), &row) != nil {
				out = append(out, s)
				continue
			}
			if row["suggestion_id"] == suggestionID && !pyTruthy(row["applied"]) {
				row["status"] = "dismissed"
				row["dismissed_at"] = nowISO()
				if reason != "" {
					row["block_reason"] = reason
				}
				found = true
				enc, _ := json.Marshal(row)
				out = append(out, string(enc))
			} else {
				out = append(out, s)
			}
		}
		if len(out) == 0 {
			return ""
		}
		return strings.Join(out, "\n") + "\n"
	})
	return found, err
}

// VerificationStamp carries stamp_verification's optional fields: nil means
// "leave the row's value untouched" (the Python keyword-None contract).
//
//	Verdict    → verify_verdict (terminal label, or interim "" cleared)
//	VerifiedAt → the terminal stamp; a truthy ISO string marks the row
//	             TERMINAL (no longer re-examined). Leave nil for an interim
//	             inconclusive re-check so the row stays pending.
//	Extensions → verify_extensions (absolute value, not a delta).
type VerificationStamp struct {
	Verdict    *string
	VerifiedAt *string
	Extensions *int
}

// StampVerification ports evolver_store.stamp_verification: durably record
// VERIFY_LEARN_ARC V2 cadence-verdict state on a suggestion. Keyed-merge
// write under the lock (same discipline as apply): rows appended/updated by
// concurrent finalizations between read and write are preserved, and a
// byte-tainted line is re-emitted verbatim, never re-dumped. Never touches
// `applied` — a degraded row is reverted by Revert; the verdict is a
// separate, orthogonal stamp. Returns true if the row was found.
func StampVerification(workspaceDir, suggestionID string, stamp VerificationStamp) bool {
	found, _ := StampVerificationChanged(workspaceDir, suggestionID, stamp)
	return found
}

// StampVerificationChanged is StampVerification reporting whether the row
// actually mutated. A TERMINAL stamp (stamp.Verdict set) is first-writer-
// wins: a row that already carries a verify_verdict is left untouched and
// reported changed=false. That is the concurrency contract the cadence
// side effects hang off (r1 QA review: two overlapping verify passes
// double-appended calibration outcomes and EVOLVER_VERDICT events, and
// the losing revert overwrote a truthful terminal stamp) — a verdict is
// rendered once, and only the renderer that landed it emits the record.
func StampVerificationChanged(workspaceDir, suggestionID string, stamp VerificationStamp) (found, changed bool) {
	p := suggestionsPath(workspaceDir)
	if _, err := os.Stat(p); err != nil {
		return false, false
	}
	_ = record.LockedRMW(p, func(old string) string {
		var out []string
		for _, line := range strings.Split(old, "\n") {
			s := strings.TrimSpace(line)
			if s == "" {
				continue
			}
			var row map[string]any
			if json.Unmarshal([]byte(s), &row) != nil {
				out = append(out, s)
				continue
			}
			if row["suggestion_id"] == suggestionID {
				found = true
				// A terminal row refuses EVERY further stamp — not just a
				// second verdict. A VerifiedAt-only stamp slipping past the
				// refusal was a latent overwrite edge (r2 review LOW-2);
				// nothing legitimately re-stamps a rendered verdict.
				prior, _ := row["verify_verdict"].(string)
				if prior != "" {
					out = append(out, s)
					continue
				}
				if stamp.Verdict != nil {
					row["verify_verdict"] = *stamp.Verdict
					changed = true
				}
				if stamp.VerifiedAt != nil {
					row["verified_at"] = *stamp.VerifiedAt
					changed = true
				}
				if stamp.Extensions != nil {
					row["verify_extensions"] = *stamp.Extensions
					changed = true
				}
				enc, _ := json.Marshal(row)
				out = append(out, string(enc))
			} else {
				out = append(out, s)
			}
		}
		if len(out) == 0 {
			return ""
		}
		return strings.Join(out, "\n") + "\n"
	})
	return found, changed
}

// BumpExtensionOrPark atomically increments a row's verify_extensions
// inside the store lock and, when the incremented value reaches max,
// parks the row terminal ("unverifiable" + verified_at) in the same
// write. The pre-fix shape — compute ext from a pre-lock snapshot, stamp
// it absolute — lost concurrent bumps (two passes both stamping 1; r1 QA
// review). changed=false means another pass already parked the row.
func BumpExtensionOrPark(workspaceDir, suggestionID string, max int, now string) (ext int, parked, changed bool) {
	p := suggestionsPath(workspaceDir)
	if _, err := os.Stat(p); err != nil {
		return 0, false, false
	}
	_ = record.LockedRMW(p, func(old string) string {
		var out []string
		for _, line := range strings.Split(old, "\n") {
			s := strings.TrimSpace(line)
			if s == "" {
				continue
			}
			var row map[string]any
			if json.Unmarshal([]byte(s), &row) != nil {
				out = append(out, s)
				continue
			}
			if row["suggestion_id"] == suggestionID {
				if prior, _ := row["verify_verdict"].(string); prior != "" {
					out = append(out, s) // already terminal
					continue
				}
				cur := 0
				if f, ok := row["verify_extensions"].(float64); ok {
					cur = int(f)
				}
				ext = cur + 1
				row["verify_extensions"] = ext
				if ext >= max {
					row["verify_verdict"] = "unverifiable"
					row["verified_at"] = now
					parked = true
				}
				changed = true
				enc, _ := json.Marshal(row)
				out = append(out, string(enc))
			} else {
				out = append(out, s)
			}
		}
		if len(out) == 0 {
			return ""
		}
		return strings.Join(out, "\n") + "\n"
	})
	return ext, parked, changed
}

// changeLogAppend writes the audit row BEFORE a mutation happens so
// changes are recoverable; failure never blocks execution (Python
// parity — the trail is best-effort, the action is not).
func changeLogAppend(workspaceDir string, d map[string]any, beforeState map[string]any) {
	text, _ := d["suggestion"].(string)
	sum := sha256.Sum256([]byte(text))
	entry := map[string]any{
		"ts":              nowISO(),
		"module":          "evolver",
		"action":          "_apply_suggestion_action",
		"category":        d["category"],
		"suggestion_id":   d["suggestion_id"],
		"target":          d["target"],
		"confidence":      d["confidence"],
		"suggestion_text": clipRunes(text, 500),
		"suggestion_hash": hex.EncodeToString(sum[:])[:12],
		"before_state":    beforeState,
	}
	raw, err := json.Marshal(entry)
	if err != nil {
		return
	}
	path := changeLogPath(workspaceDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return
	}
	_ = record.AppendRawLine(path, raw)
}

// applyAction executes the real-world effect of an approved suggestion
// (Python _apply_suggestion_action, Go-slice categories only). Returns
// true only when the category's primary action completed; callers must
// not stamp durable applied state on false.
// actionOutcome is applyAction's tri-state result. Python collapsed
// these two non-success cases differently: a guidance-only guardrail was
// still "applied" because its prose landed in playbook.md (the injected
// director surface). The Go slice does NOT port playbook.md, so a
// guidance-only guardrail has NO durable home here — stamping it
// "applied" would be a record that lies (r1 review Finding B). It is
// held instead, distinct from a genuine action failure.
type actionOutcome int

const (
	actionApplied actionOutcome = iota
	actionFailed
	actionGuidanceOnly
)

func applyAction(workspaceDir string, rec *record.Recorder, d map[string]any) actionOutcome {
	category, _ := d["category"].(string)
	if category == "" {
		category = "observation"
	}
	text, _ := d["suggestion"].(string)
	target, _ := d["target"].(string)
	if target == "" {
		target = "all"
	}
	suggestionID, _ := d["suggestion_id"].(string)
	confidence, _ := d["confidence"].(float64)

	var beforeState map[string]any
	switch category {
	case "new_guardrail":
		beforeState = map[string]any{"type": "guardrail_append"}
	case "prompt_tweak":
		beforeState = map[string]any{"type": "lesson_add"}
	}
	changeLogAppend(workspaceDir, d, beforeState)

	outcome := actionApplied
	switch category {
	case "prompt_tweak":
		// Record as a medium tiered lesson so it gets injected into
		// future prompts. Dedup-by-text via the knowledge snapshot: the
		// Python writer's reinforcement counters (an existing identical
		// lesson gets times_reinforced++) are NOT ported — an existing
		// text is treated as already-live guidance and the apply
		// succeeds as a no-op, logged.
		store := knowledge.NewStore(workspaceDir)
		snap, err := store.LessonSnapshot()
		if err != nil {
			fmt.Fprintf(os.Stderr, "[evolver] prompt_tweak apply failed (lesson snapshot): %v\n", err)
			return actionFailed
		}
		if snap.Texts[text] {
			fmt.Fprintf(os.Stderr, "[evolver] prompt_tweak %s: identical lesson already live — "+
				"apply is a no-op (reinforcement counters unported)\n", suggestionID)
			break
		}
		taskType := target
		if taskType == "" || taskType == "all" {
			taskType = "general"
		}
		tl := knowledge.TieredLesson{
			LessonID:   record.NewID() + record.NewID()[:4],
			TaskType:   taskType,
			Outcome:    "evolver_suggestion",
			Lesson:     text,
			SourceGoal: "evolver-" + suggestionID,
			Confidence: confidence,
			Tier:       "medium",
			Score:      confidence,
			// Transaction time: decay reads last_reinforced (the pack
			// importer's fair-hearing rationale applies to mints too).
			LastReinforced:  nowISO()[:10],
			RecordedAt:      nowISO(),
			EvidenceSources: []any{},
			LessonType:      "practice",
			Imported:        map[string]any{},
			// NOT provisional (Python comment): evolver suggestions
			// have their own behavioral verify lifecycle, and this
			// category exists to be injected. §5 cut B producer stamp:
			// minted_by makes evolver traces Δ-measurable as a class.
			Provisional:    false,
			MintedFrom:     "",
			MintedBy:       "evolver",
			Contested:      map[string]any{},
			MergedVariants: []string{},
			DeltaEvidence:  map[string]any{},
			Grounding:      []map[string]any{},
			Canon:          map[string]any{},
		}
		if err := store.AppendMediumLesson(tl); err != nil {
			fmt.Fprintf(os.Stderr, "[evolver] prompt_tweak apply failed (lesson write): %v\n", err)
			return actionFailed
		}

	case "new_guardrail":
		// Append to dynamic-constraints.jsonl — loaded by the PYTHON
		// runtime's constraint.py (shared workspace), which matches
		// `pattern` as a regex against step text. No pattern (or an
		// invalid one) means no row — the guidance-only outcome, never
		// a rule that can't fire. Validation caveat, named: Go's RE2
		// accepts a strict subset of Python's re syntax, so a Go-valid
		// pattern is (near-)always Python-valid, but a Python-valid
		// pattern using lookaround would be refused here — refusing is
		// the conservative direction for a row Python will execute.
		pattern := strings.TrimSpace(stringOr(d["pattern"]))
		if pattern == "" {
			fmt.Fprintf(os.Stderr, "[evolver] new_guardrail %s has no matchable pattern — "+
				"guidance only, no durable home in the Go slice (playbook unported)\n", suggestionID)
			return actionGuidanceOnly
		}
		if _, err := regexp.Compile("(?i)" + pattern); err != nil {
			fmt.Fprintf(os.Stderr, "[evolver] new_guardrail %s pattern is not a valid regex (%v) — "+
				"guidance only, no durable home in the Go slice (playbook unported)\n", suggestionID, err)
			return actionGuidanceOnly
		}
		// Idempotent by source==id: a retry after a partial apply (the
		// row's `applied` stamp failed to persist) must not append a
		// second identical constraint row (r1 review QA #1). Python has
		// no such dedup — a named hardening divergence.
		if constraintRowExists(workspaceDir, suggestionID) {
			fmt.Fprintf(os.Stderr, "[evolver] new_guardrail %s constraint row already present — "+
				"apply is an idempotent no-op\n", suggestionID)
			break
		}
		entry := map[string]any{
			"pattern": pattern,
			"risk":    "MEDIUM",
			"detail":  fmt.Sprintf("evolver guardrail (id=%s): %s", suggestionID, clipRunes(text, 80)),
			"source":  suggestionID,
			// Epoch seconds: constraint._load_dynamic_constraints'
			// TTL check compares against time.time() — an ISO string
			// here silently discarded the whole lane (Python lesson,
			// carried in the row format).
			"added_at":     float64(time.Now().Unix()),
			"added_at_iso": nowISO(),
		}
		raw, _ := json.Marshal(entry)
		dcPath := dynamicConstraintsPath(workspaceDir)
		if err := os.MkdirAll(filepath.Dir(dcPath), 0o755); err != nil {
			fmt.Fprintf(os.Stderr, "[evolver] new_guardrail apply failed: %v\n", err)
			return actionFailed
		}
		if err := record.AppendRawLine(dcPath, raw); err != nil {
			fmt.Fprintf(os.Stderr, "[evolver] new_guardrail apply failed: %v\n", err)
			return actionFailed
		}

	case "observation":
		// no action needed
	default:
		// Unreachable from Apply (unported categories are held before
		// reaching here), kept as a guard against a future caller.
		fmt.Fprintf(os.Stderr, "[evolver] applyAction: category %q has no Go handler\n", category)
		return actionFailed
	}

	// Captain's log: evolver applied a suggestion.
	subject := target
	if subject == "" {
		subject = category
	}
	_ = rec.Event("EVOLVER_APPLIED", subject,
		fmt.Sprintf("Applied %s suggestion (confidence: %.2f). %s",
			category, confidence, clipRunes(text, 100)),
		map[string]any{"suggestion_id": suggestionID, "category": category, "confidence": confidence},
		"")

	// Python also appends prompt_tweak/new_guardrail/observation text
	// (confidence >= 0.7) to the director's playbook.md — the playbook
	// surface is unported; named in the package doc. (This is why a
	// guidance-only guardrail returns actionGuidanceOnly above rather
	// than actionApplied: Python's stamp was honest because the prose
	// landed in the playbook; Go has no such durable home yet.)
	return outcome
}

func stringOr(v any) string {
	s, _ := v.(string)
	return s
}

// constraintRowExists reports whether dynamic-constraints.jsonl already
// carries a row whose source is this suggestion id — the apply/revert
// key. Tolerant read (torn line skipped); missing file = false.
//
// Scope is PRESENCE-ONLY, by design: it covers the crash/partial-apply
// window (row written, `applied` stamp not yet persisted → retry). It
// does NOT check the row's pattern or its 30-day TTL (constraint.py load
// filter), so it is not meant for re-applying an old expired id — an
// edge that requires `applied` to stay false for weeks (r2 review LOW,
// accepted-named). NOTE: this presence check is narrower than Revert's
// guardrail matcher (which also accepts the "evolver:<id>" legacy form
// and a pattern-text fallback) on purpose — this only needs to recognize
// a row THIS apply just wrote, which always carries source==id.
func constraintRowExists(workspaceDir, suggestionID string) bool {
	raw, err := os.ReadFile(dynamicConstraintsPath(workspaceDir))
	if err != nil {
		return false
	}
	for _, line := range strings.Split(string(raw), "\n") {
		s := strings.TrimSpace(line)
		if s == "" {
			continue
		}
		var row map[string]any
		if json.Unmarshal([]byte(s), &row) != nil {
			continue
		}
		if src, _ := row["source"].(string); src == suggestionID {
			return true
		}
	}
	return false
}

// Apply marks a suggestion applied by executing its action and
// rewriting its row (Python apply_suggestion). manual=true means a
// human explicitly asked (CLI review path) — that bypasses the
// guardrail hold (the review IS the gate) but never the injection
// guard, which protects against bad content regardless of who asks.
//
// Returns (found, error): found=false when no row carries the id. The
// durable applied state is what IsApplied reads afterwards — callers
// auto-applying should check both (run loop parity).
func Apply(workspaceDir string, rec *record.Recorder, cfg map[string]any,
	suggestionID string, manual bool) (bool, error) {
	p := suggestionsPath(workspaceDir)
	// Snapshot read (no lock) to find the target: the decision work
	// below can take time, so it runs outside the critical section; the
	// final update is a keyed merge under the lock.
	var d map[string]any
	for _, m := range readRows(p) {
		if m["suggestion_id"] == suggestionID {
			d = m
			break
		}
	}
	if d == nil {
		return false, nil
	}
	// Re-applying a live row must be a no-op: besides replaying the
	// mutation, a second apply could rewrite applied_manually and
	// corrupt the authority provenance that decides auto-revert.
	if pyTruthy(d["applied"]) {
		return true, nil
	}

	category := stringOr(d["category"])
	if category == "" {
		category = "observation"
	}
	text := stringOr(d["suggestion"])

	// Injection guard, FAIL-CLOSED: scan suggestion text before any
	// action. (Go's ScanContent cannot throw, so the Python
	// scan-failed arm has no Go twin — the scan itself is the gate.)
	scan := guard.ScanContent(text, "internal")
	if !scan.SafeToAutoApply() {
		d["applied"] = false
		d["status"] = "injection_risk_blocked"
		finding := ""
		if len(scan.Findings) > 0 {
			finding = clipRunes(scan.Findings[0], 120)
		}
		d["block_reason"] = "injection_guard: " + finding
	} else {
		switch category {
		case "skill_pattern":
			// NOT PORTED: skills store + test gate. Held, never
			// silently "applied" without effect.
			d["applied"] = false
			d["status"] = "held_for_review"
			d["block_reason"] = "skill_pattern apply engine is not ported in the Go slice — " +
				"apply via the Python CLI (shared store)"
		case "sub_mission":
			// NOT PORTED: goal enqueue. Same hold shape as Python's
			// default (auto_enqueue_signals off).
			d["applied"] = false
			d["status"] = "held_for_review"
			d["block_reason"] = "sub_mission enqueue is not ported in the Go slice — " +
				"run it via the Python CLI (shared store)"
		case "inspection_finding":
			// Inspector-authored rows land in the SHARED suggestions
			// store (inspector.saveSuggestions), so a human running
			// `maro evolve -apply <id>` on one reaches here. They are
			// informational (confidence 0.7, no action to run) — held,
			// not action_failed (r1 review QA #4: the old default arm's
			// "unreachable" claim was false for these rows).
			d["applied"] = false
			d["status"] = "held_for_review"
			d["block_reason"] = "inspection_finding is informational — nothing to apply; " +
				"it surfaces friction for a human to act on"
		case "cost_optimization":
			// KNOWN Python category. Python stamps it
			// "pending_human_review" (evolver_store.py) — NOT
			// "held_for_review" (which Python reserves for skill_pattern/
			// sub_mission/guardrail-gate). The shared store's operator
			// dashboard (observe.py) counts pending_human_review as the
			// "needs triage" bucket, so the literal must match byte-for-
			// byte or Go-touched rows vanish from that metric (r3 review).
			d["applied"] = false
			d["status"] = "pending_human_review"
			d["block_reason"] = "cost_optimization has no auto-apply handler — review manually " +
				"(or apply via the Python CLI, shared store)"
		case "crystallization":
			d["applied"] = false
			d["status"] = "pending_human_review"
			d["block_reason"] = "crystallization requires human review — run " +
				"`maro-memory canon-candidates` via the Python CLI (shared store)"
		case "new_guardrail":
			// Guardrails can permanently block execution paths, so the
			// gate is an explicit opt-in: manual apply → the review is
			// the gate; MARO_AUTO_APPLY_GUARDRAILS=1/0 overrides;
			// config evolver.auto_apply defaults false = held.
			shouldApply := manual
			if !shouldApply {
				switch os.Getenv("MARO_AUTO_APPLY_GUARDRAILS") {
				case "1":
					shouldApply = true
				case "0":
					shouldApply = false
				default:
					shouldApply = config.Get(cfg, "evolver.auto_apply", false)
				}
			}
			if shouldApply {
				stampAction(workspaceDir, rec, d)
			} else {
				d["applied"] = false
				d["status"] = "held_for_review"
				d["block_reason"] = "new_guardrail held for review: auto-apply is off by " +
					"default (apply via `maro evolve -apply <id>`, or set " +
					"config evolver.auto_apply: true / MARO_AUTO_APPLY_GUARDRAILS=1)"
			}
		default:
			// prompt_tweak, observation, and anything unrecognized
			// (Python's else arm applies too; Go's applyAction refuses
			// unknown categories into action_failed rather than
			// stamping a no-op success).
			stampAction(workspaceDir, rec, d)
		}
		if applied, _ := d["applied"].(bool); applied {
			// Apply timestamp lives HERE, not (only) in the captain's
			// log — the log is visibility, never the source of truth
			// for a system function.
			d["applied_at"] = nowISO()
			d["applied_manually"] = manual
		}
	}

	// Keyed merge under the lock: replace only this suggestion's line;
	// rows appended or updated concurrently are preserved, and a line
	// that vanished between snapshot and merge is re-added.
	updated, err := json.Marshal(d)
	if err != nil {
		return true, err
	}
	merr := record.LockedRMW(p, func(old string) string {
		var out []string
		replaced := false
		for _, line := range strings.Split(old, "\n") {
			s := strings.TrimSpace(line)
			if s == "" {
				continue
			}
			var row map[string]any
			if json.Unmarshal([]byte(s), &row) == nil && row["suggestion_id"] == suggestionID {
				out = append(out, string(updated))
				replaced = true
				continue
			}
			out = append(out, s)
		}
		if !replaced {
			out = append(out, string(updated))
		}
		if len(out) == 0 {
			return ""
		}
		return strings.Join(out, "\n") + "\n"
	})
	return true, merr
}

// stampAction runs applyAction and stamps the row's applied/status
// fields from its tri-state result. actionGuidanceOnly is NOT "applied":
// the Go slice has no playbook surface to carry guidance-only guardrails,
// so the row is HELD (visible, retryable) rather than stamped with a
// success it never earned (r1 review Finding B).
func stampAction(workspaceDir string, rec *record.Recorder, d map[string]any) {
	switch applyAction(workspaceDir, rec, d) {
	case actionApplied:
		d["applied"] = true
		delete(d, "status")
		delete(d, "block_reason")
	case actionGuidanceOnly:
		d["applied"] = false
		d["status"] = "held_for_review"
		d["block_reason"] = "new_guardrail has no matchable regex pattern — guidance only; " +
			"the Go slice has no playbook surface, so apply the prose via the Python CLI (shared store)"
	default: // actionFailed
		d["applied"] = false
		d["status"] = "action_failed"
	}
}

// RevertResult mirrors Python revert_suggestion's dict.
type RevertResult struct {
	Reverted bool `json:"reverted"`
	// Behavioral = did we actually undo the change's effect, not just
	// flip bookkeeping? True only for structural rollbacks (guardrail
	// removal here). lesson_add marks applied=false but leaves the
	// behavioral influence in place until it decays — callers that
	// rely on a real undo must key off this, not Reverted.
	Behavioral bool   `json:"behavioral"`
	Category   string `json:"category"`
	Detail     string `json:"detail"`
	// NothingToRevert = the row was not applied when we looked — usually
	// because a concurrent cadence already reverted it. Callers must treat
	// this as "handled elsewhere", NEVER as a failed revert (r1 QA review:
	// the losing cadence stamped degraded_revert_failed and fired a false
	// BLOCKING escalation over a revert that had in fact succeeded).
	NothingToRevert bool `json:"-"`
}

// Revert reverses a previously applied suggestion via the change_log
// audit trail (Go slice: lesson_add bookkeeping-only, guardrail_append
// removes the constraint row; skill categories refuse — engine
// unported).
func Revert(workspaceDir string, rec *record.Recorder, suggestionID string) RevertResult {
	// Only an APPLIED suggestion can be reverted. changeLogAppend writes
	// an audit row at the TOP of applyAction — before the outcome is
	// known — so a guidance-only/held or action_failed suggestion still
	// has a change_log entry with a mutation before_state it never
	// performed. Reverting off that entry would stamp status="reverted"
	// over an honest held_for_review/action_failed status (r2 review: a
	// record that lies). Guard on the durable applied flag instead.
	if !IsApplied(workspaceDir, suggestionID) {
		return RevertResult{Category: "", NothingToRevert: true,
			Detail: fmt.Sprintf("suggestion_id %s is not applied — nothing to revert", suggestionID)}
	}
	entries := readRows(changeLogPath(workspaceDir))
	var match map[string]any
	for i := len(entries) - 1; i >= 0; i-- {
		if entries[i]["suggestion_id"] == suggestionID {
			match = entries[i]
			break
		}
	}
	if match == nil {
		return RevertResult{Category: "",
			Detail: fmt.Sprintf("suggestion_id %s not found in change_log", suggestionID)}
	}
	category := stringOr(match["category"])
	target := stringOr(match["target"])
	detail := ""
	behavioral := false

	switch category {
	case "skill_pattern":
		return RevertResult{Category: category,
			Detail: "skill revert engine is not ported in the Go slice — revert via the Python CLI"}
	case "new_guardrail":
		dcPath := dynamicConstraintsPath(workspaceDir)
		if _, err := os.Stat(dcPath); err == nil {
			suggestionText := stringOr(match["suggestion_text"])
			removed := false
			rerr := record.LockedRMW(dcPath, func(old string) string {
				var out []string
				for _, line := range strings.Split(old, "\n") {
					s := strings.TrimSpace(line)
					if s == "" {
						continue
					}
					var row map[string]any
					if json.Unmarshal([]byte(s), &row) == nil {
						src := stringOr(row["source"])
						// Apply writes source=<id>; fork-point Python's
						// revert matched only "evolver:<id>" (a key
						// mismatch that made current-format rows
						// unremovable — backport-correction candidate,
						// named in PORT.md). Match both forms plus the
						// legacy prose-in-pattern fallback.
						if src == suggestionID || src == "evolver:"+suggestionID ||
							(suggestionText != "" && stringOr(row["pattern"]) == clipRunes(suggestionText, 200)) {
							removed = true
							continue
						}
					}
					out = append(out, s)
				}
				if len(out) == 0 {
					return ""
				}
				return strings.Join(out, "\n") + "\n"
			})
			if rerr != nil {
				return RevertResult{Category: category, Detail: "revert failed: " + rerr.Error()}
			}
			if removed {
				detail = "removed dynamic constraint"
				behavioral = true
			} else {
				detail = "dynamic constraint not found (may have expired)"
			}
		} else {
			// The constraints file is gone entirely (never written, or the
			// whole lane pruned). Same honest detail as the row-absent branch
			// rather than a silent empty-detail success (r13 QA #8).
			detail = "dynamic constraint not found (may have expired)"
		}
	case "prompt_tweak":
		detail = "prompt_tweak lessons are append-only; lesson will decay naturally"
	default:
		detail = fmt.Sprintf("no revert action for category '%s'", category)
	}

	// Mark suggestion as not applied. A failure here does not undo the
	// behavioral revert above — say so in the detail (Python r17: a
	// swallowed failure left the row applied=True inside a result
	// claiming the revert completed).
	storePersisted := true
	p := suggestionsPath(workspaceDir)
	if _, err := os.Stat(p); err == nil {
		merr := record.LockedRMW(p, func(old string) string {
			var out []string
			for _, line := range strings.Split(old, "\n") {
				s := strings.TrimSpace(line)
				if s == "" {
					continue
				}
				var row map[string]any
				if json.Unmarshal([]byte(s), &row) != nil {
					out = append(out, s)
					continue
				}
				if row["suggestion_id"] == suggestionID {
					row["applied"] = false
					row["status"] = "reverted"
				}
				enc, _ := json.Marshal(row)
				out = append(out, string(enc))
			}
			if len(out) == 0 {
				return ""
			}
			return strings.Join(out, "\n") + "\n"
		})
		if merr != nil {
			detail += "; suggestion store NOT updated — still marked applied"
			storePersisted = false
		}
	}

	// The event carries the persisted truth so a consumer keying on event
	// TYPE (not the free-text detail) isn't misled into counting a non-
	// persisted revert as done (r4 review — the EVOLVER_REVERTED name
	// otherwise lies when storePersisted is false).
	_ = rec.Event("EVOLVER_REVERTED", suggestionID,
		fmt.Sprintf("Reverted suggestion %s (%s): %s", suggestionID, category, detail),
		map[string]any{"suggestion_id": suggestionID, "category": category,
			"target": target, "persisted": storePersisted},
		"")
	// Reverted reflects the DURABLE state: if the store write failed, the
	// row still reads applied=true, so IsApplied stays true and the
	// caller must not be told the revert completed (r3 review — a
	// Reverted:true over a non-persisted revert is a record that lies,
	// and would also let a second Revert past the IsApplied guard).
	return RevertResult{Reverted: storePersisted, Behavioral: behavioral, Category: category, Detail: detail}
}
