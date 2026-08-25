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
// Telegram notify; step traces in the outcomes summary (no Go
// step-trace store). The playbook.md append IS ported now
// (internal/playbook), and with it the guidance-only guardrail's
// durable home — see applyAction's tail.
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
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/guard"
	"github.com/slycrel/maro-orchestration/go/internal/knowledge"
	"github.com/slycrel/maro-orchestration/go/internal/playbook"
	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
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

// nowISO is `datetime.now(timezone.utc).isoformat()` — every timestamp
// this package writes (dismissed_at, applied_at, added_at_iso, the
// change_log ts) is that call in Python, verbatim, at all four sites.
//
// It delegates rather than formatting, because the two spellings are not
// the same string. RFC3339Nano — what this was — renders UTC as "Z" and
// keeps up to NINE fractional digits with trailing zeros trimmed;
// isoformat renders "+00:00", keeps exactly six, and omits the fraction
// entirely when it is zero. Three differences in a field that lands in a
// store the Python runtime reads, and only one of them is cosmetic:
// datetime.fromisoformat rejected a "Z" suffix outright before CPython
// 3.11 (measured accepted on 3.14 here, truncating the extra digits), so
// a Go-written stamp is a stamp an older reader cannot parse at all.
//
// pyval.NowISO is the port-wide spelling and predates this file. Writing
// a second one locally is the fourth lens — a helper you did not look for
// is a helper you will write again — and there are five more local copies
// plus four inline RFC3339Nano stamps still to classify (see PORT.md).
func nowISO() string { return pyval.NowISO(time.Now().UTC()) }

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
		// json.dumps in the Python writer's key order (mission-r8).
		out, _ := pyval.DumpsCompactPy(pyval.Obj{
			{Key: "runs_since_evolve", Val: count},
			{Key: "updated_at", Val: nowISO()},
		})
		return out
	})
	return fired, err
}

// readRows tolerantly reads all parseable rows from a jsonl file
// (missing file = empty store; a torn line costs one row).
// readRowsAnnounced is the read-only half of readRowsOrdered: Python's
// `_read_store` again, for the four call sites that only INSPECT rows.
//
// Unordered on purpose. Key order is only observable when a row is
// written back, and handing an ordered row to a caller that will never
// re-emit it invites the mistake the ordered reader's doc warns about —
// projecting to a map, mutating, and re-emitting alphabetized.
//
// What this replaced was a hand-rolled ReadFile + strings.Split + TrimSpace
// + json.Unmarshal loop, which diverged from `read_jsonl_announced` three
// ways: it dropped the loss announcement entirely, it treated a
// non-dict row and a torn row as the same nothing, and its number type
// was float64 where every other reader in this port now yields
// json.Number. The `what` label is the one the Python call site passes,
// so an operator sees the same loader named in the same warning.
func readRowsAnnounced(path, what string) []map[string]any {
	rows, warn := record.ReadAllAnnounced(path, what)
	if warn != "" {
		fmt.Fprintln(os.Stderr, "[evolver] "+warn)
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
// pyTruthy delegates to the one implementation of Python's bool().
//
// It used to be a private copy, and the copy had no `case int`: every
// value arriving from encoding/json is a float64, so the gap was
// invisible on the read path, but a count built in Go is an `int` and
// bool(0) came back TRUE. Three packages had grown their own copy of
// this — with three different case sets — while a complete one already
// sat in pyval (mission-r9: the copies are the bug, not the case sets).
func pyTruthy(v any) bool { return pyval.Truthy(v) }

// LoadSuggestions returns up to limit suggestions, newest first.
func LoadSuggestions(workspaceDir string, limit int) []Suggestion {
	path := suggestionsPath(workspaceDir)
	rows := readRowsAnnounced(path, "load_suggestions")
	// `_rows_as(p, "load_suggestions", Suggestion.from_dict)` is TWO
	// readers, and the port had only the first. from_dict is
	// `cls(**{k: d[k] for k in fields if k in d})`, so a row missing any of
	// the seven fields with no default raises TypeError, is EXCLUDED, and
	// is counted as schema drift under its own warning. rowToSuggestion
	// never fails, so the port kept the row and zero-filled it — the same
	// half-a-reader shape that made load_outcomes count rows CPython
	// excludes (see record.LoadOutcomes).
	//
	// The exclusion happens BEFORE the reversal and the limit, so a
	// drifted row does not consume a window slot.
	rows = keepLoadableSuggestions(rows, path, "load_suggestions")
	var out []Suggestion
	for i := len(rows) - 1; i >= 0; i-- {
		out = append(out, rowToSuggestion(rows[i]))
		if limit > 0 && len(out) >= limit {
			break
		}
	}
	return out
}

// suggestionRequiredFields are evolver_store.Suggestion's fields with NO
// default, in declaration order — the order CPython names them in when it
// says which arguments were missing.
var suggestionRequiredFields = []string{
	"suggestion_id", "category", "target", "suggestion", "failure_pattern",
	"confidence", "outcomes_analyzed",
}

// missingSuggestionFields is the PRESENCE test from_dict performs. A
// present null is a value, not an absence — a dataclass does not enforce
// its annotations, so `Suggestion(confidence=None, ...)` constructs.
func missingSuggestionFields(row map[string]any) []string {
	var missing []string
	for _, f := range suggestionRequiredFields {
		if _, ok := row[f]; !ok {
			missing = append(missing, f)
		}
	}
	return missing
}

// keepLoadableSuggestions applies the dataclass filter and announces the
// drift in CPython's own sentence — separate from the framing warning,
// because a row that is not JSON is corruption and a row the dataclass
// rejects is drift, and drift is the one that grows quietly as the schema
// moves.
func keepLoadableSuggestions(rows []map[string]any, path, what string) []map[string]any {
	kept := make([]map[string]any, 0, len(rows))
	drifted, firstErr := 0, ""
	for _, r := range rows {
		missing := missingSuggestionFields(r)
		if len(missing) == 0 {
			kept = append(kept, r)
			continue
		}
		drifted++
		if firstErr == "" {
			firstErr = "TypeError: " + record.PyMissingArgsMessage("Suggestion", missing)
		}
	}
	if drifted > 0 {
		fmt.Fprintf(os.Stderr, "[evolver] %s: %d row(s) in %s are JSON but "+
			"not loadable under the current schema — excluded from the %d "+
			"returned (first: %s)\n", what, drifted, path, len(kept), firstErr)
	}
	return kept
}

// GetSuggestion returns the current on-disk row for one id, or nil —
// a single-row uncapped lookup that never drops the row behind a
// newest-N window (Python get_suggestion).
func GetSuggestion(workspaceDir, suggestionID string) *Suggestion {
	for _, m := range readRowsAnnounced(suggestionsPath(workspaceDir), "get_suggestion") {
		if m["suggestion_id"] == suggestionID {
			// from_dict RAISES on a drifted row here, and CPython catches
			// it and treats the row as ABSENT. That is not a nicety: this
			// is the lookup the V2 auto-revert guard uses to re-confirm
			// authority just before an irreversible revert, and a port that
			// zero-filled the row instead handed that guard an
			// applied_manually of false — routing a human-applied row into
			// the auto-revert branch. Same unsafe direction as the r1
			// security finding at this same function, reached by a
			// different missing coercion.
			if missing := missingSuggestionFields(m); len(missing) > 0 {
				fmt.Fprintf(os.Stderr, "[evolver] get_suggestion: row %s is "+
					"JSON but not loadable as Suggestion (TypeError: %s) — "+
					"treating as absent\n", suggestionID,
					record.PyMissingArgsMessage("Suggestion", missing))
				return nil
			}
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
// The Python key is a TUPLE of three str() COERCIONS, the last one
// stripped: `(str(d.get("category","")), str(d.get("target","")),
// str(d.get("suggestion","")).strip())`. Two divergences hid in the Go
// spelling, and both fail in the same direction — toward a MISSED dedup,
// which is the direction that resurrects a reviewed suggestion:
//
//   - a `.(string)` type assertion yields "" for a numeric or boolean
//     field, so two rows with different non-string categories collided on
//     the same key here and had distinct keys there. Or, worse, a row whose
//     category is 0 keyed as "" and matched an unrelated row.
//   - strings.TrimSpace does not know U+001C-U+001F, so a suggestion whose
//     text ends in one keyed differently from its already-stored twin and
//     the row was appended again.
//
// The 81-duplicate calibration-row bug this key was built to end is the
// standing reminder of what a missed dedup costs.
//   - and `d.get(k, "")` defaults on ABSENCE, never on a present null. A
//     row carrying `"category": null` keys as "None" in Python — that is
//     `str(None)` — where a Go map lookup gives nil for both cases and the
//     port keyed it "". Same missed-dedup direction, reached by the same
//     route as three sites in the pack importer (r5 review): the two
//     spellings collapse into one Go expression, so a fix that looks
//     total covers only the half the author had in mind.
func contentKeyOf(row pyval.Obj) contentKeyTuple {
	return contentKey(pyStrGetV(row, "category"), pyStrGetV(row, "target"),
		pyStrGetV(row, "suggestion"))
}

// pyStrGetV is `str(d.get(k, ""))` — the presence rule and the coercion,
// in one place so neither can be applied without the other.
func pyStrGetV(row pyval.Obj, key string) string {
	v, present := row.Get(key)
	if !present {
		return ""
	}
	return pyStrValue(v)
}

// contentKeyTuple is Python's 3-TUPLE, which is what _content_key returns.
//
// It was a string joined on U+0000 for one round. A tuple cannot be
// confused by its own field contents and a joined string can: category
// "x\x00y" with an empty target produces the same bytes as category "x"
// with target "y\x00", so one of the two suggestions was silently dropped
// from the shared store as a duplicate of the other (adversarial r3,
// MEDIUM). A Go comparable struct is the tuple, exactly — same equality,
// same map-key behaviour, no separator to collide on.
type contentKeyTuple struct {
	category   string
	target     string
	suggestion string
}

func contentKey(category, target, suggestion string) contentKeyTuple {
	return contentKeyTuple{category, target, pytext.Strip(suggestion)}
}

// pyStrValue is Python's `str(v)` on a value that is PRESENT — including a
// present null, which is the string "None" and not the empty string. The
// absence rule is pyStrGetV's job; splitting them is what stops the next
// caller from getting one and thinking it got both.
//
// FOUR str()-shaped helpers now live in this file and they are NOT
// interchangeable — read this before reaching for one:
//
//   - pyStrValue (here)  `str(v)`              — a present null gives "None"
//   - pyStrGetV          `str(d.get(k, ""))`   — absent gives "", null "None"
//   - pyStrKey           `str(d.get(k) or "")` — every FALSY value gives ""
//   - stringOr           a bare type assertion — "" for any non-string
//
// stringOr is the one that is not a Python spelling at all; it survives
// only at sites whose Python really does compare against a string and
// where a non-string can be treated as absent. The distinction between
// the first two is the `or ""`, and it is load-bearing: str(0) is "0"
// and str(0 or "") is "". Collapsing these is the exact bug this port
// keeps re-finding — pack carries the same trio (asString / pyStrOr /
// pyStrGet), and the r5 review found it at three sites in one file.
func pyStrValue(v any) string {
	return pyval.Str(v)
}

// pyNumStr is `str()` on a number CPython has already DECODED, which is
// not the same as the literal the file carried.
//
// json.Number.String() hands back the source text, so `1.50` stayed
// "1.50" where Python says "1.5", `1e5` stayed "1e5" where Python says
// "100000.0", and `1e400` stayed "1e400" where Python says "inf". Every
// one of those is a content-key mismatch, and a content-key mismatch is a
// MISSED dedup — the direction that resurrects a suggestion someone
// already reviewed.
//
// An INTEGER literal keeps its text: Python's str(int) is exact at any
// width, and routing a 22-digit integer through float64 would round it.
// Only a literal carrying '.', 'e' or 'E' decoded to a float in the first
// place, so only those go back through FloatRepr.
func pyNumStr(t json.Number) string {
	s := t.String()
	if !strings.ContainsAny(s, ".eE") {
		return s // an int literal: str(int) is the literal, at any width
	}
	f, err := t.Float64()
	if err != nil && !math.IsInf(f, 0) {
		return s // unparseable as a float; the literal is the best answer
	}
	return pyjson.FloatRepr(f)
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
		seen := map[contentKeyTuple]bool{}
		dedupReport := record.SkipReport{}
		// THIS SCAN IS NOT ONE OF THE FOUR MERGES, and it was spelled like
		// them for a round.
		//
		// Dismiss, StampVerification, BumpExtensionOrPark and Apply each
		// port a Python merge that really is `for line in old.splitlines():
		// s = line.strip(); if not s: continue`. _save_suggestions is
		// `for d in _read_store(p, "_save_suggestions")` —
		// read_jsonl_announced, which frames on b"\n" alone, parses the
		// UNSTRIPPED line, treats only a truly empty fragment as framing,
		// and buckets its losses three ways.
		//
		// All three axes changed an answer, measured on both runtimes:
		//
		//   - the strip: a row prefixed with U+001F is stranded by CPython
		//     (its key never enters `seen`, so the duplicate IS appended)
		//     and admitted here, which SUPPRESSED the append. Two runtimes,
		//     opposite decisions, same shared store — the 81-duplicate axis
		//     running in both directions at once.
		//   - the split: the same reversal for a row carrying U+001C, which
		//     splitlines() breaks on and the store's own reader does not.
		//   - the buckets: an array row is "non-dict" and a non-UTF-8 row is
		//     "undecodable" to CPython, and both were "malformed" here, so
		//     the operator got a different sentence about the same damage.
		//     A whitespace-only line was counted by CPython and silently
		//     skipped here.
		//
		// The reader helper is still unreachable — the scan is inside
		// LockedRMW on purpose, because a `seen` built outside the lock
		// reopens the bug — so it borrows the reader's own per-line step
		// instead of re-deriving it.
		for _, line := range record.SplitStoredLines(old) {
			// Ordered, because contentKeyOf renders each field through
			// Python's str() and str() over a dict depends on insertion
			// order. A map-decoded row makes pyval.Repr refuse, so every
			// row with a dict-valued category/target/suggestion would key
			// on one sentinel string and dedup against each other.
			if m := record.CountLineOrdered(line, &dedupReport); m != nil {
				seen[contentKeyOf(m)] = true
			}
		}
		if warn := dedupReport.Announce("_save_suggestions", p); warn != "" {
			fmt.Fprintln(os.Stderr, "[evolver] "+warn)
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
			// json.dumps(asdict(s)) — the suggestions store is shared and
			// Suggestion.Confidence is a float64 that is whole at 1.0,
			// which json.Marshal writes as `1` (mission-r8).
			line, err := pyval.DumpsStruct(s)
			if err != nil {
				marshalErr = err
				continue
			}
			out += line + "\n"
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
	for _, m := range readRowsAnnounced(suggestionsPath(workspaceDir), "suggestion_is_applied") {
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
		for _, line := range rmwLines(old) {
			s := pytext.Strip(line)
			if s == "" {
				continue
			}
			// loads_clean, not json.loads. A byte-tainted line must never
			// id-match: encoding/json substitutes U+FFFD inside string
			// values, so a tainted row could match and then be re-dumped
			// as clean escapes — laundering bytes CPython preserves.
			// Falling into the preserve branch keeps the line as it is.
			row, lerr := record.LoadsCleanOrdered(s)
			if lerr != nil {
				out = append(out, s)
				continue
			}
			if val(row, "suggestion_id") == suggestionID && !pyTruthy(val(row, "applied")) {
				row.Set("status", "dismissed")
				row.Set("dismissed_at", nowISO())
				if reason != "" {
					row.Set("block_reason", reason)
				}
				found = true
				enc, _ := pyval.DumpsCompactPy(row)
				out = append(out, enc)
			} else {
				out = append(out, s)
			}
		}
		return pyJoinOrEmpty(out)
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
		for _, line := range rmwLines(old) {
			s := pytext.Strip(line)
			if s == "" {
				continue
			}
			// loads_clean, not json.loads. A byte-tainted line must never
			// id-match: encoding/json substitutes U+FFFD inside string
			// values, so a tainted row could match and then be re-dumped
			// as clean escapes — laundering bytes CPython preserves.
			// Falling into the preserve branch keeps the line as it is.
			row, lerr := record.LoadsCleanOrdered(s)
			if lerr != nil {
				out = append(out, s)
				continue
			}
			if val(row, "suggestion_id") == suggestionID {
				found = true
				// A terminal row refuses EVERY further stamp — not just a
				// second verdict. A VerifiedAt-only stamp slipping past the
				// refusal was a latent overwrite edge (r2 review LOW-2);
				// nothing legitimately re-stamps a rendered verdict.
				prior, _ := val(row, "verify_verdict").(string)
				if prior != "" {
					out = append(out, s)
					continue
				}
				if stamp.Verdict != nil {
					row.Set("verify_verdict", *stamp.Verdict)
					changed = true
				}
				if stamp.VerifiedAt != nil {
					row.Set("verified_at", *stamp.VerifiedAt)
					changed = true
				}
				if stamp.Extensions != nil {
					row.Set("verify_extensions", *stamp.Extensions)
					changed = true
				}
				enc, _ := pyval.DumpsCompactPy(row)
				out = append(out, enc)
			} else {
				out = append(out, s)
			}
		}
		return pyJoinOrEmpty(out)
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
		for _, line := range rmwLines(old) {
			s := pytext.Strip(line)
			if s == "" {
				continue
			}
			// loads_clean, not json.loads. A byte-tainted line must never
			// id-match: encoding/json substitutes U+FFFD inside string
			// values, so a tainted row could match and then be re-dumped
			// as clean escapes — laundering bytes CPython preserves.
			// Falling into the preserve branch keeps the line as it is.
			row, lerr := record.LoadsCleanOrdered(s)
			if lerr != nil {
				out = append(out, s)
				continue
			}
			if val(row, "suggestion_id") == suggestionID {
				if prior, _ := val(row, "verify_verdict").(string); prior != "" {
					out = append(out, s) // already terminal
					continue
				}
				// pyval.IntOf covers the json.Number the ordered reader
				// hands back AND the float64 the old map read produced,
				// so a store written by either runtime counts the same.
				//
				// NOT a quotation of a Python line, and the comment here
				// used to claim one that does not exist
				// (`int(row.get("verify_extensions", 0) or 0)`). The real
				// Python read is evolver_scans.py:1478,
				// `int(getattr(s, "verify_extensions", 0)) + 1` — off a
				// DATACLASS attribute, from a pre-lock snapshot. This
				// whole in-lock bump is the r1 QA hardening and has no
				// Python counterpart. In a file whose discipline is
				// quoting the line being ported, an invented citation is
				// worse than none: it is the thing the next reader
				// trusts instead of checking.
				ext = pyval.IntOf(val(row, "verify_extensions")) + 1
				row.Set("verify_extensions", ext)
				if ext >= max {
					row.Set("verify_verdict", "unverifiable")
					row.Set("verified_at", now)
					parked = true
				}
				changed = true
				enc, _ := pyval.DumpsCompactPy(row)
				out = append(out, enc)
			} else {
				out = append(out, s)
			}
		}
		return pyJoinOrEmpty(out)
	})
	return ext, parked, changed
}

// changeLogAppend writes the audit row BEFORE a mutation happens so
// changes are recoverable; failure never blocks execution (Python
// parity — the trail is best-effort, the action is not).
// It takes the COERCED fields, not the raw map. Python builds this row
// from the same locals _apply_suggestion_action already computed, and
// reading `d` again here wrote a different row: an absent confidence
// became `null` where CPython writes 0.5, and an absent category became
// `null` where CPython writes "observation" (adversarial mission-r6
// MEDIUM). Two readings of one dict is the defect; there is now one.
// changeLogAppend writes one audit row. beforeState is `any` rather than
// map[string]any because Python has THREE answers here and a Go map has two:
// a captured snapshot, an empty snapshot, and None.
//
// A typed nil map is still a map to a type switch, so pyval.FromPlain matched
// its `case map[string]any` and rendered `{}` where CPython writes `null` —
// on the most ordinary apply there is (category "observation", which captures
// no before-state at all). The r1 round graded this loss "currently
// zero-sized because both ported categories build a one-key map"; there is a
// third category, it builds no map, and what it loses is not key order but
// the value. An absent-vs-null collapse arriving through the WRITER, in the
// chunk that closed it on the reader.
func changeLogAppend(workspaceDir string, f applyFields, beforeState any) {
	sum := sha256.Sum256([]byte(f.text))
	entry := pyval.Obj{
		{Key: "ts", Val: nowISO()},
		{Key: "module", Val: "evolver"},
		{Key: "action", Val: "_apply_suggestion_action"},
		{Key: "category", Val: f.category},
		{Key: "suggestion_id", Val: f.suggestionID},
		{Key: "target", Val: f.target},
		{Key: "confidence", Val: f.confidence},
		{Key: "suggestion_text", Val: clipRunes(f.text, 500)},
		{Key: "suggestion_hash", Val: hex.EncodeToString(sum[:])[:12]},
		// before_state is a Go map and comes out key-sorted; Python's
		// order is its own dict's. Nothing joins on it — it is read as a
		// whole for recovery — so the loss is named, not chased.
		{Key: "before_state", Val: pyval.FromPlain(beforeState)},
	}
	// json.dumps, not json.Marshal: suggestion_text is LLM prose and
	// carries "->" and non-ASCII routinely, and change_log.jsonl is read
	// by both runtimes (adversarial mission-r7 HIGH).
	line, err := pyval.DumpsCompactPy(entry)
	if err != nil {
		return
	}
	raw := []byte(line)
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
	f := readApplyFields(d)
	category, text, suggestionID := f.category, f.text, f.suggestionID
	target, confidence := f.target, f.confidence

	// `any`, and it starts as an untyped nil so an uncaptured before-state
	// renders `null`. Declaring it map[string]any and leaving it unset gives
	// a TYPED nil, which is not the same value to a type switch.
	var beforeState any
	switch category {
	case "new_guardrail":
		beforeState = map[string]any{"type": "guardrail_append"}
	case "prompt_tweak":
		beforeState = map[string]any{"type": "lesson_add"}
	}
	changeLogAppend(workspaceDir, f, beforeState)

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
			Imported:        pyval.Obj{},
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
		//
		// Neither guidance-only path RETURNS. Python's logs-and-falls-
		// through, so the EVOLVER_APPLIED row and the playbook append at
		// the bottom happen for a pattern-less guardrail exactly as they
		// do for a matched one. This code used to return early — which
		// suppressed BOTH, and on a shared store that is a row Python
		// writes and this runtime does not.
		// `str(d.get("pattern", "") or "").strip()` — evolver_store.py:594.
		// That is pyStrKey (the `or ""` collapses every falsey value first)
		// and pytext.Strip (which knows U+001C-U+001F where TrimSpace does
		// not), and the port had BOTH halves wrong at once.
		//
		// stringOr is a bare type assertion, so a numeric or boolean pattern
		// became "" and the whole branch fell through to guidance-only —
		// while still stamping the row applied. Measured: `"pattern": 5`
		// makes CPython write a constraint row with "5" and this runtime
		// write none, both with `"applied": true`. And a pattern wrapped in
		// unit separators kept them here and lost them there, which is the
		// 2026-08-04 "guardrails that could never fire" failure re-created
		// in a file constraint.py reads.
		pattern := pytext.Strip(pyStrKey(d["pattern"]))
		if pattern == "" {
			fmt.Fprintf(os.Stderr, "[evolver] new_guardrail %s has no matchable pattern — "+
				"guidance only, no constraint row\n", suggestionID)
			outcome = actionGuidanceOnly
			break
		}
		if _, err := regexp.Compile("(?i)" + pattern); err != nil {
			fmt.Fprintf(os.Stderr, "[evolver] new_guardrail %s pattern is not a valid regex (%v) — "+
				"guidance only, no constraint row\n", suggestionID, err)
			outcome = actionGuidanceOnly
			break
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
		// pyval.Obj, not a map: this row is APPENDED to a store the
		// Python runtime also appends to, and a map's keys come out
		// alphabetized where Python writes them in dict-literal order
		// (pattern, risk, detail, source, added_at, added_at_iso).
		// Nothing parses the bytes — the divergence shows up in a diff
		// of the shared ledger, where every Go-written guardrail row
		// looks unlike every Python-written one.
		entry := pyval.Obj{
			{Key: "pattern", Val: pattern},
			{Key: "risk", Val: "MEDIUM"},
			{Key: "detail", Val: fmt.Sprintf("evolver guardrail (id=%s): %s", suggestionID, clipRunes(text, 80))},
			{Key: "source", Val: suggestionID},
			// Epoch seconds: constraint._load_dynamic_constraints'
			// TTL check compares against time.time() — an ISO string
			// here silently discarded the whole lane (Python lesson,
			// carried in the row format).
			// UnixNano/1e9, not float64(Unix()): Python's time.time() is a
			// float WITH sub-second precision, and truncating to whole
			// seconds wrote 1787635032.0 where CPython writes
			// 1787635032.8502514. The TTL comparison tolerates it, so
			// nothing breaks — the row just carries less than it claims,
			// and two guardrails applied inside the same second become
			// indistinguishable by time.
			{Key: "added_at", Val: float64(time.Now().UnixNano()) / 1e9},
			{Key: "added_at_iso", Val: nowISO()},
		}
		// `added_at` is float64(unix seconds): a whole float on EVERY row,
		// which json.Marshal wrote as an int and json.dumps writes with a
		// `.0`. This file gates constraint enforcement on both sides, so
		// the type of that field is load-bearing (mission-r8).
		line, _ := pyval.DumpsCompactPy(entry)
		raw := []byte(line)
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

	// The director's playbook is the durable home for the applied
	// insight, and for a guardrail whose pattern could not be matched it
	// is the ONLY home — Python's own comment at the guardrail branch
	// says so: "the prose still lands in the playbook below, which is the
	// honest home for a guardrail we can't match".
	//
	// Failure is swallowed to match Python's bare `except Exception:
	// pass`. It is NOT swallowed for the purpose of the return value: a
	// guidance-only guardrail is only upgraded to "applied" when the
	// prose actually landed.
	landed := false
	if confidence >= 0.7 && playbookSection[category] != "" {
		if err := playbook.Append(workspaceDir, rec, text,
			playbookSection[category], "evolver:"+suggestionID,
			pyStrKey(d["playbook_key"])); err == nil {
			landed = true
		}
	}
	if outcome == actionGuidanceOnly && landed {
		// The r1 Finding B divergence closes HERE, and only here. The
		// hold existed because stamping "applied" with no durable home
		// was a record that lies. The home now exists, so the honest
		// stamp is the same one Python writes.
		outcome = actionApplied
	}
	return outcome
}

// playbookSection maps an applied suggestion's category to the playbook
// section its prose belongs in, and doubles as the membership test for
// which categories get appended at all.
//
// Python spells the membership test and the mapping separately, with a
// `.get(category, "Learned")` default that is unreachable — every member
// of the tuple is a key of the dict. Folding them removes a default that
// could never fire; the three entries are the contract.
//
// "Learned" is deliberately NOT one of the four seed sections, so an
// observation entry ranks as LEARNED in injection and outranks the
// curated seed. That is Python's behaviour, not an accident of this port.
var playbookSection = map[string]string{
	"prompt_tweak":  "Execution",
	"new_guardrail": "Quality",
	"observation":   "Learned",
}

// pyStrKey ports `str(d.get("playbook_key", "") or "")`.
//
// The alarm key decides whether a later reading REPLACES this entry in
// place or accretes beside it, so silently yielding "" for a non-string
// turns an alarm into a permanent insight. Python's `or ""` collapses
// every falsey value first, then `str()` renders whatever survives.
//
// Numbers arrive as json.Number, because every reader that feeds this
// decodes with UseNumber. That arm was MISSING and the value fell to the
// default "" — the same class as the verify_extensions bug, one file over
// and found by the same review: swapping the decoder changed the type at
// every site that reads a number, and two of them read one.
//
// The float64 arm is a LOSSY FALLBACK, not an agreeing sibling, and the
// sentence here used to claim the opposite on both halves.
//
// It is not live: every production caller reads off a row decoded with
// UseNumber (pyStrKey from objMap(*d) via readRowsOrdered, pyStrValue from
// contentKeyOf via record.LoadsClean), so a float64 arrives only from a
// caller holding a plain json.Unmarshal value, of which there are none.
//
// And it could not agree if it were. A float64 has already thrown away the
// int/float distinction CPython's json keeps, so a stored `5` renders "5.0"
// here and "5" there — no spelling inside this arm can fix that, because
// the information is gone before the arm is reached. It stays as a defined
// answer for an undefined input, and it is named as such (adversarial r2,
// L3).
func pyStrKey(v any) string {
	// pyval.StrOrEmpty IS `str(v or "")`, and delegating removes the arm
	// that made this a live defect: the `default: return ""` below dropped
	// LISTS and DICTS, so a new_guardrail whose pattern the model answered
	// as ["rm -rf"] recorded as APPLIED with no constraint row installed,
	// while CPython stored the pattern "['rm -rf']". The store said the
	// guardrail was live and nothing could match against it (adversarial
	// r3, MEDIUM/HIGH).
	//
	// The lossy float64 arm goes with it — pyval.Repr renders a float64
	// through the same FloatRepr, and the int/float information is already
	// gone before either of them is reached, which is a property of the
	// caller's decoder and not of this function.
	return pyval.StrOrEmpty(v)
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
	var d pyval.Obj
	for _, m := range readRowsOrdered(p, "apply_suggestion") {
		if val(m, "suggestion_id") == suggestionID {
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
	if pyTruthy(val(d, "applied")) {
		return true, nil
	}

	category := stringOr(val(d, "category"))
	if category == "" {
		category = "observation"
	}
	text := stringOr(val(d, "suggestion"))

	// Injection guard, FAIL-CLOSED: scan suggestion text before any
	// action. (Go's ScanContent cannot throw, so the Python
	// scan-failed arm has no Go twin — the scan itself is the gate.)
	scan := guard.ScanContent(text, "internal")
	if !scan.SafeToAutoApply() {
		d.Set("applied", false)
		d.Set("status", "injection_risk_blocked")
		finding := ""
		if len(scan.Findings) > 0 {
			finding = clipRunes(scan.Findings[0], 120)
		}
		d.Set("block_reason", "injection_guard: "+finding)
	} else {
		switch category {
		case "skill_pattern":
			// NOT PORTED: skills store + test gate. Held, never
			// silently "applied" without effect.
			d.Set("applied", false)
			d.Set("status", "held_for_review")
			d.Set("block_reason", "skill_pattern apply engine is not ported in the Go slice — "+
				"apply via the Python CLI (shared store)")
		case "sub_mission":
			// NOT PORTED: goal enqueue. Same hold shape as Python's
			// default (auto_enqueue_signals off).
			d.Set("applied", false)
			d.Set("status", "held_for_review")
			d.Set("block_reason", "sub_mission enqueue is not ported in the Go slice — "+
				"run it via the Python CLI (shared store)")
		case "inspection_finding":
			// Inspector-authored rows land in the SHARED suggestions
			// store (inspector.saveSuggestions), so a human running
			// `maro evolve -apply <id>` on one reaches here. They are
			// informational (confidence 0.7, no action to run) — held,
			// not action_failed (r1 review QA #4: the old default arm's
			// "unreachable" claim was false for these rows).
			d.Set("applied", false)
			d.Set("status", "held_for_review")
			d.Set("block_reason", "inspection_finding is informational — nothing to apply; "+
				"it surfaces friction for a human to act on")
		case "cost_optimization":
			// KNOWN Python category. Python stamps it
			// "pending_human_review" (evolver_store.py) — NOT
			// "held_for_review" (which Python reserves for skill_pattern/
			// sub_mission/guardrail-gate). The shared store's operator
			// dashboard (observe.py) counts pending_human_review as the
			// "needs triage" bucket, so the literal must match byte-for-
			// byte or Go-touched rows vanish from that metric (r3 review).
			d.Set("applied", false)
			d.Set("status", "pending_human_review")
			d.Set("block_reason", "cost_optimization has no auto-apply handler — review manually "+
				"(or apply via the Python CLI, shared store)")
		case "crystallization":
			d.Set("applied", false)
			d.Set("status", "pending_human_review")
			d.Set("block_reason", "crystallization requires human review — run "+
				"`maro-memory canon-candidates` via the Python CLI (shared store)")
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
				stampAction(workspaceDir, rec, &d)
			} else {
				d.Set("applied", false)
				d.Set("status", "held_for_review")
				d.Set("block_reason", "new_guardrail held for review: auto-apply is off by "+
					"default (apply via `maro evolve -apply <id>`, or set "+
					"config evolver.auto_apply: true / MARO_AUTO_APPLY_GUARDRAILS=1)")
			}
		default:
			// prompt_tweak, observation, and anything unrecognized
			// (Python's else arm applies too; Go's applyAction refuses
			// unknown categories into action_failed rather than
			// stamping a no-op success).
			stampAction(workspaceDir, rec, &d)
		}
		if applied, _ := val(d, "applied").(bool); applied {
			// Apply timestamp lives HERE, not (only) in the captain's
			// log — the log is visibility, never the source of truth
			// for a system function.
			d.Set("applied_at", nowISO())
			d.Set("applied_manually", manual)
		}
	}

	// Keyed merge under the lock: replace only this suggestion's line;
	// rows appended or updated concurrently are preserved, and a line
	// that vanished between snapshot and merge is re-added.
	updated, err := pyval.DumpsCompactPy(d)
	if err != nil {
		return true, err
	}
	merr := record.LockedRMW(p, func(old string) string {
		var out []string
		replaced := false
		for _, line := range rmwLines(old) {
			s := pytext.Strip(line)
			if s == "" {
				continue
			}
			// loads_clean for the same reason as its three siblings: this
			// branch REPLACES the matched line, so a tainted row that
			// id-matched would be silently swapped for a re-dumped one.
			if row, lerr := record.LoadsClean(s); lerr == nil &&
				row["suggestion_id"] == suggestionID {
				out = append(out, updated)
				replaced = true
				continue
			}
			out = append(out, s)
		}
		if !replaced {
			out = append(out, updated)
		}
		return pyJoinOrEmpty(out)
	})
	return true, merr
}

// stampAction runs applyAction and stamps the row's applied/status
// fields from its tri-state result.
//
// actionGuidanceOnly now reaches stampAction only when the prose did NOT
// land in the playbook — which, with the playbook ported, means the
// suggestion sat below the 0.7 confidence gate that both runtimes apply
// before appending. Above that gate a guidance-only guardrail applies,
// because its prose has a durable home.
//
// Below it, the row is still HELD (visible, retryable) rather than
// stamped with a success it never earned (r1 review Finding B). That is
// a NAMED divergence: Python stamps applied there anyway, with the prose
// going nowhere.
// stampAction takes the row by POINTER because Set may append, which
// reallocates the slice — a by-value pyval.Obj would take the mutation on
// a copy and the caller would write the unstamped row. That is the one
// ergonomic cost of an ordered row over a map, and it is worth naming: a
// map hands its mutations back for free, so this is the seam where an
// order-preserving port can silently lose a write.
func stampAction(workspaceDir string, rec *record.Recorder, d *pyval.Obj) {
	// applyAction and readApplyFields only READ, so they keep their map
	// signatures and get a projection. Projecting once here rather than
	// converting them keeps the ordered type at the sites that re-emit
	// the row, which is the only place order is observable.
	switch applyAction(workspaceDir, rec, objMap(*d)) {
	case actionApplied:
		d.Set("applied", true)
		// `d.pop("status", None)` and NOTHING ELSE — all five of Python's
		// success arms (evolver_store.py:767/796/817/837/874) pop exactly
		// this one key. The port also popped block_reason, which is a
		// tidier row and a byte divergence: a suggestion held once and
		// applied later keeps its stale block_reason in CPython's copy
		// and lost it in this one. Reached by an entirely ordinary row —
		// no exotic character, no concurrency — and unnamed, which is the
		// half that makes it a bug rather than a decision.
		d.Pop("status")
	case actionGuidanceOnly:
		d.Set("applied", false)
		d.Set("status", "held_for_review")
		// The reason must name the cause that ACTUALLY held this row, and
		// there are three: below the confidence gate, a category with no
		// playbook section, or an Append that failed (a 30s fail-closed
		// lock timeout against a concurrent Python writer, an unreadable
		// file, a full disk). The old string asserted the first
		// unconditionally, so a high-confidence guardrail held by a lock
		// timeout sent an operator to lower a threshold it had already
		// cleared (adversarial r10 LOW). This row is durable and
		// operator-facing; a confident wrong cause is worse than a vague
		// right one.
		//
		// confidence and category are read off `d` with the same
		// coercions applyAction uses (store.go:612, :622), so this cannot
		// drift into a second source of truth for the gate.
		gate := readApplyFields(objMap(*d))
		confidence, category := gate.confidence, gate.category
		reason := "new_guardrail has no matchable regex pattern — guidance only, " +
			"and the prose did not reach the playbook, so it has no durable home"
		switch {
		case confidence < 0.7:
			reason += " (confidence " + pyjson.FloatRepr(confidence) +
				" is below the 0.7 append gate)"
		case playbookSection[category] == "":
			reason += " (category " + category + " has no playbook section)"
		default:
			reason += " (the playbook append failed — see the warning log)"
		}
		d.Set("block_reason", reason+"; review manually")
	default: // actionFailed
		d.Set("applied", false)
		d.Set("status", "action_failed")
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
	entries := readRowsAnnounced(changeLogPath(workspaceDir), "revert_suggestion")
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
				// _drop_constraint is the ODD one of the five merges, and
				// it is odd in two ways this port had normalized away:
				// it does NOT strip, and it does NOT skip blanks. Every
				// line it does not drop is re-emitted RAW.
				//
				// So the port deleted blank lines and trimmed whitespace
				// off preserved rows — a byte divergence in a shared
				// store that needs no exotic character at all, just an
				// ordinary blank line. A rewrite is not a read: what the
				// loop does with a line it is not interested in IS the
				// output.
				for _, line := range rmwLines(old) {
					// loads_clean on the RAW line: a byte-tainted row must
					// never match the drop key, so it falls through to the
					// preserve branch verbatim rather than being re-dumped
					// as clean escapes.
					if row, lerr := record.LoadsClean(line); lerr == nil {
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
					out = append(out, line)
				}
				return pyJoinOrEmpty(out)
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
			return markRevertedMerge(old, suggestionID)
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

// applyFields holds the five suggestion fields _apply_suggestion_action
// coerces once and then uses everywhere — the action, the audit row, and
// the operator-facing block reason.
type applyFields struct {
	category     string
	text         string
	target       string
	suggestionID string
	confidence   float64
}

// readApplyFields is Python's five lines at the top of
// _apply_suggestion_action, coercion for coercion:
//
//	category      = d.get("category", "observation")
//	suggestion_text = d.get("suggestion", "")
//	target        = d.get("target", "all")
//	suggestion_id = d.get("suggestion_id", "")
//	confidence    = float(d.get("confidence", 0.5))
//
// Every one of those is `.get`, which keys on PRESENCE. The Go idiom it
// replaces defaulted on an EMPTY stored value too, so `"category": ""`
// was written to change_log.jsonl as "observation" here and as "" there.
//
// The confidence line is the one place this deliberately does NOT match:
// Python's bare float() raises TypeError on a stored null and ValueError
// on "abc", straight out of a function whose docstring says "Never
// raises" — so a null confidence crashes the Python apply path and
// returns 0.5 here. That is a Python bug and the fix belongs there
// (a safe_float makes both runtimes agree); refusing to crash is not a
// divergence worth porting backwards. For every value float() ACCEPTS,
// including the numeric strings a bare .(float64) used to zero, SafeFloat
// agrees with it exactly — a claim mission-r7 falsified once ("0x1p-2",
// which ParseFloat took and float() refuses) and which holds again now
// that toFloat rejects hex. Re-measure it; do not read it.
func readApplyFields(d map[string]any) applyFields {
	str := func(key, def string) string {
		s, ok := pyval.GetOr(d, key, def).(string)
		if !ok {
			return ""
		}
		return s
	}
	return applyFields{
		category:     str("category", "observation"),
		text:         str("suggestion", ""),
		target:       str("target", "all"),
		suggestionID: str("suggestion_id", ""),
		confidence: pyval.SafeFloat(
			pyval.GetOr(d, "confidence", 0.5), 0.5, nil, nil),
	}
}

// rmwLines is `old.splitlines()` — the ONE line rule for every
// read-modify-write callback in this file.
//
// It exists because there were seven hand-written copies of it, all of them
// spelled `strings.Split(old, "\n")`, and every one of them REWRITES a
// store the Python runtime reads. A read that splits differently returns a
// different list; a rewrite that splits differently WRITES a different file.
// A row carrying \x0b or \x1c is two lines to CPython's merge and one to
// this one, so the two runtimes reflow the same store into different bytes
// — and then each reads the other's reflow.
//
// `old` arrives from record.LockedRMW as raw bytes, which is correct: the
// Python side is `locked_rmw`, whose read is
// `errors="surrogateescape"`, not `read_text`. Undecodable bytes must
// SURVIVE a rewrite, not abort it, so there is deliberately no strict
// decode here — the opposite of the graduation readers, and for the
// opposite reason.
func rmwLines(old string) []string { return pytext.SplitLines(old) }

// pyJoinOrEmpty is `"\n".join(out) + "\n" if out else ""` — four of the
// five Python merges.
func pyJoinOrEmpty(out []string) string {
	if len(out) == 0 {
		return ""
	}
	return strings.Join(out, "\n") + "\n"
}

// pyJoinAlways is `"\n".join(out) + "\n"`, unconditionally — _mark_reverted
// alone. On an empty store that is "\n", not "".
//
// A one-byte difference is worth its own function precisely because it
// looks like a typo: the next reader who "tidies" the two into one has
// reintroduced a divergence, and a named pair makes the asymmetry
// deliberate rather than accidental.
func pyJoinAlways(out []string) string {
	return strings.Join(out, "\n") + "\n"
}

// val is `d[k]` on an ordered row: the value, or nil when the key is
// absent — the same one-valued answer a Go map gives, deliberately.
//
// The two-valued form is pyval.Obj.Get, and it is the one to reach for
// whenever ABSENT and NULL have to differ (see pyStrGetV). This spelling
// exists for the majority of sites, where the row is being matched or
// truth-tested and both cases mean the same thing.
func val(o pyval.Obj, key string) any {
	v, _ := o.Get(key)
	return v
}

// objMap projects an ordered row down to a plain map for the read-only
// helpers that were written against one.
//
// One direction only, and that is the point: order is destroyed on the
// way down and cannot be recovered on the way back, so a caller that
// projects, mutates the map, and re-emits has silently alphabetized the
// row. Every mutation stays on the pyval.Obj.
func objMap(o pyval.Obj) map[string]any {
	m := make(map[string]any, len(o))
	for _, f := range o {
		m[f.Key] = f.Val
	}
	return m
}

// readRowsOrdered is the snapshot read for a row that will be WRITTEN
// BACK: Python's `_read_store` is `read_jsonl_announced`, so this is
// record.ReadAllAnnouncedOrdered, and `what` names the loader in the
// warning exactly as the Python call site does.
//
// It replaces a hand-rolled split-and-Unmarshal loop that diverged from
// its Python three ways at once — it dropped the loss announcement (the
// fourth-lens class: a helper you did not look for is a helper you will
// write again), it split on "\n" where Python's reader does not, and it
// returned unordered rows that Apply then re-emitted alphabetized.
//
// The warning goes to stderr rather than being returned, because both
// callers are deep inside a decision path with nowhere to put it and the
// alternative — dropping it — is the divergence being fixed.
func readRowsOrdered(path, what string) []pyval.Obj {
	rows, warn := record.ReadAllAnnouncedOrdered(path, what)
	if warn != "" {
		fmt.Fprintln(os.Stderr, "[evolver] "+warn)
	}
	return rows
}

// markRevertedMerge is _mark_reverted's merge, named so a test can
// drive it without a store, a lock and an IsApplied check in the way.
//
// It was inline, and that made the SITE untestable even though both
// join helpers had tests: swapping pyJoinAlways for pyJoinOrEmpty here
// survived the whole suite, because Revert only reaches this merge past
// an IsApplied check that reads the same file — so an empty store exits
// early and the one case the asymmetry exists for was unreachable from
// outside. A helper that fixes a class does not fix the class; it fixes
// the callers that reach it, and a test of the helper is not a test of
// the call.
func markRevertedMerge(old, suggestionID string) string {
	var out []string
	// _mark_reverted is the other odd merge, and it differs from
	// its four siblings in three ways at once:
	//
	//   - it does not skip blanks. A blank line reaches
	//     loads_clean(""), raises, and lands in the PRESERVE
	//     branch, which appends the RAW line — so the blank
	//     survives the rewrite.
	//   - the strip is for the PARSE only; what gets preserved is
	//     the untouched line.
	//   - it returns "\n".join(...) + "\n" UNCONDITIONALLY. On an
	//     empty store CPython writes "\n" where the four siblings
	//     write "". One byte, in a file the other runtime reads.
	//
	// This branch re-dumps EVERY parseable row, so a byte-tainted
	// line must be refused INTO the preserve branch or it would be
	// laundered into clean escapes — which is exactly why Python
	// spells the parse as loads_clean and not json.loads.
	for _, line := range rmwLines(old) {
		row, lerr := record.LoadsCleanOrdered(pytext.Strip(line))
		if lerr != nil {
			out = append(out, line)
			continue
		}
		if val(row, "suggestion_id") == suggestionID {
			row.Set("applied", false)
			row.Set("status", "reverted")
		}
		enc, eerr := pyval.DumpsCompactPy(row)
		if eerr != nil {
			out = append(out, line)
			continue
		}
		out = append(out, enc)
	}
	return pyJoinAlways(out)
}
